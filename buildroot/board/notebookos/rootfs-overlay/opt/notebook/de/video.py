#!/usr/bin/env python3
"""
Video Editor — the Notebook OS storyboard / timeline video app (native GTK).

A three-pane workspace: a Media bin (with a real Import browser and a palette of
transitions), a central 16:9 preview with transport controls, and a Properties
panel. Beneath it a switchable Storyboard / Timeline strip.

The project opens EMPTY on a fresh install (no media, no clips). Import scans the
Home folder for real video/image/audio files and adds them to the bin; a bin
clip is placed onto a storyboard slot by selecting it, then clicking the slot.
Per-clip title/duration/transition are edited in Properties, and the whole
project (bin + storyboard) persists to $NB_HOME/.config/notebook/video.json.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, Pango, GLib  # noqa: E402

import os
import json
import shutil
import tempfile
import subprocess
import threading
import time

import nbapp
import nbpicker
import nbicons
import nbvideo
import nbtransitions
from nbi18n import _t  # noqa: E402

# ---- optional media libs (guarded) ----------------------------------------
# Both the preview frames and the finished export are produced by the ffmpeg
# CLI (probed at call time via shutil.which), so a host without ffmpeg degrades
# to a neutral 'engine unavailable' state instead of crashing. The one library
# dependency is GdkPixbuf, used to wrap a decoded PNG frame for the on-screen
# Image: it is guaranteed on the built guest but the host running the selftests
# may lack it, so its import is guarded and PIXBUF_OK gates every use below.
# Video frames decode ASYNCHRONOUSLY (an ffmpeg subprocess polled by
# GLib.timeout, never a thread), so a slow or large clip never blocks the GTK
# main loop.
PIXBUF_OK = False
try:
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf  # noqa: E402
    PIXBUF_OK = True
except (ImportError, ValueError):
    GdkPixbuf = None

DE_DIR = os.path.dirname(os.path.abspath(__file__))

INK = "#1A1916"
MUTED = "#6E695E"
FAINT = "#9A9484"
GHOST = "#9A9484"
RED = "#C8341E"

# ---- persistence ----------------------------------------------------------
# The whole project (media bin + the 8 storyboard slots) round-trips through a
# single private file, matching the widgets.py/tasks.py pattern: load on
# __init__ (empty on first run — no seed), save on every mutation and on close.
HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
CFG_DIR = os.path.join(HOME, ".config", "notebook")
PROJECT_FILE = os.path.join(CFG_DIR, "video.json")
# File ▸ Open/Save named project files live here; the CFG_DIR/video.json
# autosave above is only session recovery for the working project.
# Project files live in the standard Documents folder (matching sequencer.py):
# no app may create a non-standard top-level folder in $NB_HOME — the home
# dir's visible contents must stay the standard set (Desktop/Documents/Music/
# Pictures/Videos) plus Applications. Rendered exports still go to Videos.
PROJ_DIR = os.path.join(HOME, "Documents")
# File ▸ Export Video renders finished .mp4 files here (matches Music ▸ Music).
VIDEOS_DIR = os.path.join(HOME, "Videos")

SLOTS = 8

# How many steps back Undo can go. Each step is one serialised project (a small
# dict of the bin + the clip list), so the whole history costs almost nothing.
UNDO_DEPTH = 40

# A decoded preview frame is scaled to fit inside the 16:9 stage, leaving room
# for the metadata caption beneath it. Kept modest so the frame never forces the
# centre column wider than a small native panel (1366x768/1280x800) can spare
# once the two side rails are accounted for. The export renders at a fixed
# 720p/24fps and is independent of this on-screen preview cap.
PREV_W, PREV_H = 640, 360
# Frame sizes an export can be rendered at. Every clip is scaled and letterboxed
# to the chosen one, so a project can be re-exported at another size unchanged.
# The frame rate is the same for all three: it is what every filter offset in
# the render is computed against, so varying it would vary nothing a viewer can
# see while multiplying the ways the graph can be mis-timed.
EXPORT_SIZES = ((640, 360), (1280, 720), (1920, 1080))
EXPORT_W, EXPORT_H, EXPORT_FPS = 1280, 720, 24
EXPORT_BG = "0x16150F"   # the dark stage colour, reused for letterbox padding

TRANSITIONS = [
    ("trfade", "Fade"), ("trdissolve", "Dissolve"), ("trwipe", "Wipe"),
    ("trslide", "Slide"), ("triris", "Iris"), ("trblack", "To Black"),
]
TRANS_NAME = dict(TRANSITIONS)
TRANS_KEYS = set(TRANS_NAME)
# Each storyboard transition key maps to the ffmpeg `xfade` transition it
# renders as (the export folds adjacent clips with xfade, audio with
# acrossfade). Anything unmapped falls back to a hard cut (plain concat).
XFADE_NAME = {
    "trfade": "fade", "trdissolve": "dissolve", "trwipe": "wipeleft",
    "trslide": "slideleft", "triris": "circleopen", "trblack": "fadeblack",
}
# How long a transition runs, in seconds. Clamped per boundary so it never
# exceeds either adjacent clip; a boundary that clamps below this floor is
# rendered as a hard cut instead of a degenerate sub-frame crossfade.
TRANS_SECS = 1.0
TRANS_FLOOR = 0.2
# Common audio format the export normalises every lane clip to before mixing.
AUDIO_RATE = 48000

# Recognised media extensions (grouped so the bin can pick an icon + label).
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mpg", ".mpeg"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}
AUDIO_EXT = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".opus"}
KIND_ICON = {"video": "video", "image": "media", "audio": "music"}
KIND_LABEL = {"video": "Video", "image": "Image", "audio": "Audio"}
# Default seconds a freshly-placed clip runs (the user edits it in Properties).
KIND_DUR = {"video": 5, "image": 4, "audio": 5}


def _ext_kind(ext):
    if ext in VIDEO_EXT:
        return "video"
    if ext in IMAGE_EXT:
        return "image"
    if ext in AUDIO_EXT:
        return "audio"
    return None


# ---- Movie-Maker feature vocabularies -------------------------------------
# Visual effects a clip can carry (Movie Maker's "Visual Effects" gallery).
# Each maps to an ffmpeg video-filter fragment applied to the clip BEFORE it is
# scaled/padded to the export frame, so it composes with transitions untouched.
EFFECTS = [
    ("none", "None"), ("bw", "Black & White"), ("sepia", "Sepia"),
    ("warm", "Warm"), ("cool", "Cool"), ("bright", "Brighten"),
    ("dark", "Darken"), ("mirror", "Mirror"), ("flipv", "Flip"),
    ("vignette", "Vignette"), ("blur", "Soft Blur"),
]
EFFECT_NAME = dict(EFFECTS)
EFFECT_KEYS = set(EFFECT_NAME)
# ffmpeg filter fragment per effect (empty for "none").
EFFECT_VF = {
    "none": "",
    "bw": "hue=s=0",
    "sepia": ("colorchannelmixer="
              "0.393:0.769:0.189:0:0.349:0.686:0.168:0:0.272:0.534:0.131"),
    "warm": "colorbalance=rm=0.12:gm=0.03:bm=-0.10",
    "cool": "colorbalance=rm=-0.10:gm=0.0:bm=0.12",
    "bright": "eq=brightness=0.10:saturation=1.05",
    "dark": "eq=brightness=-0.12:saturation=0.98",
    "mirror": "hflip",
    "flipv": "vflip",
    "vignette": "vignette=PI/4",
    "blur": "gblur=sigma=6",
}

# Playback speeds a video clip can run at (Movie Maker's speed control). The
# clip's on-timeline LENGTH stays what the user set; speed changes how much
# source is consumed (2x pulls twice the footage into the same length).
SPEEDS = [(0.5, "0.5×"), (1.0, "1×"), (1.5, "1.5×"), (2.0, "2×")]
SPEED_VALUES = [s for s, _ in SPEEDS]

# Pan-and-zoom (Ken Burns) presets for stills.
KENBURNS = [("none", "None"), ("in", "Zoom In"), ("out", "Zoom Out"),
            ("left", "Pan Left"), ("right", "Pan Right")]
KENBURNS_NAME = dict(KENBURNS)
KENBURNS_KEYS = set(KENBURNS_NAME)

# A standalone title / credits card (Movie Maker's Title & Credits) is a clip
# with no bin media — it renders a full-frame card from cairo at export time.
TITLE_DUR = 4
CARD_BG = "#1A1916"      # the dark stage tone, matching the preview screen


def _new_clip(media_idx, kind, dur):
    """A fresh media clip with every Movie-Maker attribute at its default."""
    return {
        "media": media_idx, "kind": kind, "start": 0.0,
        "duration": max(1, int(dur)), "title": "", "transition": None,
        "effect": "none", "volume": 1.0, "mute": False, "afade": False,
        "vfade": False, "kenburns": "none", "speed": 1.0,
    }


def _new_title(text="Title", sub="", dur=TITLE_DUR):
    """A fresh standalone title / credits card clip."""
    return {
        "media": None, "kind": "title", "start": 0.0, "duration": dur,
        "title": "", "transition": None, "effect": "none", "volume": 1.0,
        "mute": False, "afade": False, "vfade": True, "kenburns": "none",
        "speed": 1.0, "cardtext": text, "cardsub": sub,
    }


class StackHistory:
    """Presents this app's own two-stack history to nbapp.undo_menu_items.

    The editor banks whole-project snapshots (with a coalescing key) rather
    than the text checkpoints nbapp.UndoHistory takes, so it cannot use that
    class directly - but its Edit menu must be worded exactly as Novel,
    Journal, Academics, Illustrator and the Sequencer word theirs, naming what
    a step would take back ("Undo Delete Clip"). This is the six methods
    undo_menu_items asks for, over the lists the app already keeps. A step
    banked without a name gives the bare "Undo", which is right for nudging a
    volume slider."""

    def __init__(self, app):
        # the app, not its lists: New / Open REPLACE the history
        self._app = app

    def can_undo(self):
        return bool(self._app._undo)

    def can_redo(self):
        return bool(self._app._redo)

    def undo(self):
        return self._app._undo_action()

    def redo(self):
        return self._app._redo_action()

    @staticmethod
    def _top(names):
        # translate, THEN trim the ellipsis - exactly what
        # nbapp.UndoHistory._label_at does, so a name taken from a menu label
        # ("Import Media...") reads as an action, not as a menu entry
        if not names or not names[-1]:
            return None
        return _t(names[-1]).rstrip(" \u2026")

    def undo_label(self):
        return self._top(self._app._undo_names)

    def redo_label(self):
        return self._top(self._app._redo_names)


class VideoEditor(nbapp.AppWindow):
    app_name = "Video Editor"
    menus = ("File", "Edit", "View", "Clip")

    def __init__(self):
        super().__init__()
        self._install_css()
        self._measure_panel()

        # interactive state (set before builders run so they can register
        # their own widgets into these collections)
        self._playing = False           # transport play/stop toggle
        self._path = None               # current named project file, if any
        self._zoom = 1.0                # timeline/storyboard zoom factor
        self._sel_cell = None           # selected storyboard slot index
        self.sel_media = None           # selected media-bin item index
        self._active_transition = None  # chosen transition key
        self._suspend_prop = False      # guard while loading Properties fields
        self._trans_cells = {}          # transition key -> palette cell Box
        self._story_cells = []          # storyboard slot Boxes, in order
        self._trans_dots = []           # connector Boxes between slots (SLOTS-1)
        self._tick_labels = []          # ruler tick Labels
        self._lanes = {}                # timeline lane name -> lane Box
        self._play_img = None           # the play/stop Image in the transport
        # Keyed on (media path, trim-in point), NOT the path: two clips cut
        # from one file are two different pictures. Keying on the path alone
        # meant splitting a clip left both halves showing the same frame.
        self._frame_cache = {}          # frame key -> decoded preview pixbuf/False
        self._card_thumbs = {}          # frame key -> storyboard-card pixbuf
        self._card_imgs = {}            # frame key -> the card Images on screen
        # export engine state (all None/0 until a render is running)
        self._exp_layer = None          # the Export overlay layer, if open
        self._exp_proc = None           # the running ffmpeg subprocess
        self._exp_poll_id = 0           # GLib.timeout source polling the render
        self._exp_done = False          # a finished render (Render button reveals)
        self._exp_progress_file = None  # ffmpeg -progress scratch file
        self._exp_err_file = None       # ffmpeg stderr scratch file
        self._exp_errfh = None          # open handle to the stderr scratch file
        self._exp_build_gen = 0         # supersedes a pending async cmd-build
        self._exp_preparing = False     # async cmd-build in flight ('Preparing…')
        self._exp_tmp_imgs = []         # generated caption/title-card PNGs
        # transport playback state (a GLib.timeout clock — no threads)
        self._play_id = 0               # the running playback timeout, if any
        self._play_pos = 0.0            # playback clock, seconds into the reel
        self._live_clip = None          # index of the clip gtksink is streaming
        self._player = None             # nbvideo.Playback, built with the preview
        self._play_last = 0             # monotonic time of the last tick
        self._playhead = None           # the timeline playhead Box (moved live)
        # asynchronous preview-decode state (ffmpeg subprocess + poll)
        self._pv_proc = None            # the running frame-decode subprocess
        self._pv_poll_id = 0            # GLib.timeout source polling that decode
        self._pv_tmp = None             # its scratch PNG path
        self._pv_key = None             # frame key currently being decoded
        self._pv_path = None            # media path currently being decoded
        self._pv_queue = []             # frame keys still owed a card thumbnail
        # in-window confirm overlay (destructive-action guard)
        self._confirm_layer = None
        # undo / redo history (see _push_undo)
        self._undo = []
        self._redo = []
        # what each step would take back, for the Edit menu ("Undo Delete
        # Clip"). Kept beside the snapshots, which are unpacked positionally.
        # None = a step that needs no name (dragging a slider).
        self._undo_names = []
        self._redo_names = []
        self.history = StackHistory(self)
        self._undo_busy = False    # true while restoring, so nothing re-records

        # model — loaded from disk (empty on first run, no seed)
        self._bin = []                  # [{path, name, kind, dur, srcdur}]
        self.clips = []                 # dynamic ordered sequence of clip dicts
        self.music = None               # background track dict, or None
        self._sel_music = False         # music strip is the current selection
        self._srcdur_cache = {}         # media path -> probed source seconds
        # Export frame size. Read by every filter builder (never the module
        # constants) so the whole render follows the chosen size, and persisted
        # with the project so a re-export keeps it.
        self._out_w, self._out_h = EXPORT_W, EXPORT_H
        self._load_project()

        # ---------------- upper region: three columns ----------------
        upper = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        upper.set_vexpand(True)
        self.content.pack_start(upper, True, True, 0)

        upper.pack_start(self._media_bin(), False, False, 0)
        upper.pack_start(self._preview(), True, True, 0)
        upper.pack_start(self._properties(), False, False, 0)

        # ---------------- timeline region ----------------
        self.content.pack_start(self._timeline(), False, False, 0)

        # paint the loaded model into every pane, then settle Properties on the
        # "nothing selected" state.
        self._render_all()
        self._load_props(None)
        self._update_project_name()

        # Final flush on close so the last edit is never lost.
        self.connect("destroy", self._on_destroy)

    # ================= panel metrics =================
    def _measure_panel(self):
        """Size the three columns, the preview frame and the timeline strip from
        the REAL panel, never a hardcoded 1920.

        The workspace is three side-by-side columns over a fixed strip, so its
        minimum size is the sum of parts — and none of them could shrink. The
        preview is the worst offender: a decoded frame goes into a Gtk.Image,
        whose minimum size IS the pixbuf's, so a fixed 640x360 frame forced the
        whole window to 1360x830. On a 1024x768 or 1366x768 panel the Properties
        column and the bottom of the storyboard were then simply off-screen, and
        selecting a clip was what triggered it. Everything below is therefore
        derived from the live panel and capped at the roomy desktop values, so a
        small panel gets a smaller (but complete) workspace and a large one is
        unchanged."""
        self._sw, self._sh = nbapp.screen_size()
        self._bin_w = max(250, min(340, int(self._sw * 0.18)))
        self._prop_w = max(250, min(300, int(self._sw * 0.16)))
        self._prev_pad = 24 if self._sw < 1500 else 40
        # The strip cannot go below ~280 (its ruler + four lanes), so that is
        # the floor; a roomy panel gets the full 290.
        self._tl_h = max(280, min(290, int(self._sh * 0.27)))
        # width left for the centre column once the two rails and the stage
        # margins are paid for (a little slack so nothing lands flush)
        avail_w = (self._sw - self._bin_w - self._prop_w
                   - 2 * self._prev_pad - 16)
        # height left once the desktop panel (28), the app menu bar (46), the
        # timeline strip and the preview column's own furniture (stage margins,
        # the caption line under the frame and the transport row) are paid for
        avail_h = self._sh - 28 - 46 - self._tl_h - 152
        w = max(320, min(PREV_W, avail_w))
        h = max(180, min(int(w * 9 // 16), avail_h))
        self._prev_w = min(w, int(h * 16 // 9))
        self._prev_h = h

    # ================= media bin =================
    def _media_bin(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_size_request(self._bin_w, -1)
        box.get_style_context().add_class("mediabin")

        # header
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        head.get_style_context().add_class("binhead")
        title = Gtk.Label(label=_t("MEDIA"), xalign=0)
        title.get_style_context().add_class("bintitle")
        head.pack_start(title, True, True, 0)

        imp = Gtk.Button()
        imp.set_relief(Gtk.ReliefStyle.NONE)
        imp.get_style_context().add_class("importbtn")
        ih = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        ih.pack_start(nbicons.image("plus", 13, INK),
                      False, False, 0)
        ih.pack_start(Gtk.Label(label=_t("Import")), False, False, 0)
        imp.add(ih)
        imp.connect("clicked", self._on_import)
        head.pack_end(imp, False, False, 0)
        box.pack_start(head, False, False, 0)

        # bin body (fills) — filled by _render_bin(): either the honest empty
        # state or the live list of imported media items.
        self._bin_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._bin_body.set_vexpand(True)
        box.pack_start(self._bin_body, True, True, 0)

        # transitions
        tr = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        tr.get_style_context().add_class("transwrap")
        tl = Gtk.Label(label=_t("TRANSITIONS"), xalign=0)
        tl.get_style_context().add_class("translabel")
        tr.pack_start(tl, False, False, 0)

        grid = Gtk.Grid()
        grid.set_column_homogeneous(True)
        grid.set_row_spacing(8)
        grid.set_column_spacing(8)
        for i, (icon, name) in enumerate(TRANSITIONS):
            cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            cell.get_style_context().add_class("transcell")
            cell.set_opacity(0.55)
            img = nbicons.image(icon, 20, INK)
            img.set_halign(Gtk.Align.CENTER)
            cell.pack_start(img, False, False, 0)
            lab = Gtk.Label(label=_t(name))
            lab.get_style_context().add_class("transname")
            cell.pack_start(lab, False, False, 0)
            self._trans_cells[icon] = cell
            evt = Gtk.Button()
            evt.set_relief(Gtk.ReliefStyle.NONE)
            evt.get_style_context().add_class("videohit")
            evt.get_style_context().add_class("transhit")
            evt.set_tooltip_text(_t("Apply %s transition") % _t(name))
            evt.add(cell)
            evt.connect("clicked", self._on_transition_click, icon)
            grid.attach(evt, i % 3, i // 3, 1, 1)
        tr.pack_start(grid, False, False, 0)
        box.pack_start(tr, False, False, 0)
        return box

    def _render_bin(self):
        """(Re)draw the media bin body from self._bin. Empty -> the honest
        'No media imported' state; otherwise a selectable list where clicking a
        row selects it (ready to click a storyboard slot to place it)."""
        for c in self._bin_body.get_children():
            self._bin_body.remove(c)

        if not self._bin:
            empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            empty.set_valign(Gtk.Align.CENTER)
            empty.set_vexpand(True)
            empty.set_margin_start(20)
            empty.set_margin_end(20)
            film = nbicons.image("video", 40, GHOST)
            film.set_halign(Gtk.Align.CENTER)
            empty.pack_start(film, False, False, 0)
            e1 = Gtk.Label(label=_t("No media imported"))
            e1.get_style_context().add_class("emptytitle")
            empty.pack_start(e1, False, False, 0)
            self._bin_body.pack_start(empty, True, True, 0)
            self._bin_body.show_all()
            return

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.get_style_context().add_class("binscroll")
        lst = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for i, m in enumerate(self._bin):
            evt = Gtk.Button()
            evt.set_relief(Gtk.ReliefStyle.NONE)
            evt.get_style_context().add_class("videohit")
            evt.get_style_context().add_class("binhit")
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=11)
            row.get_style_context().add_class("binrow")
            if i == self.sel_media:
                row.get_style_context().add_class("binsel")
            ico = nbicons.image(KIND_ICON.get(m["kind"], "video"), 19, INK)
            ico.set_valign(Gtk.Align.CENTER)
            row.pack_start(ico, False, False, 0)
            txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            nm = Gtk.Label(label=m["name"], xalign=0)
            nm.get_style_context().add_class("binname")
            nm.set_ellipsize(Pango.EllipsizeMode.END)
            nm.set_max_width_chars(22)
            txt.pack_start(nm, False, False, 0)
            meta = Gtk.Label(label=KIND_LABEL.get(m["kind"], "Clip"), xalign=0)
            meta.get_style_context().add_class("binmeta")
            txt.pack_start(meta, False, False, 0)
            row.pack_start(txt, True, True, 0)
            evt.set_tooltip_text(_t("Select %s (%s)") %
                                 (m["name"], _t(KIND_LABEL.get(m["kind"], "Clip"))))
            evt.add(row)
            evt.connect("clicked", lambda _w, idx=i: self._on_bin_click(idx))
            lst.pack_start(evt, False, False, 0)
        scroll.add(lst)
        self._bin_body.pack_start(scroll, True, True, 0)
        self._bin_body.show_all()

    def _on_bin_click(self, idx):
        # Toggle selection of a bin item. A selected item is what a storyboard
        # click will place; clicking it again clears the selection.
        self.sel_media = None if self.sel_media == idx else idx
        self._render_bin()
        self._render_story()   # empty slots switch to the "Place here" prompt

    # ================= preview =================
    def _preview(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.get_style_context().add_class("previewcol")

        stage = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        stage.set_vexpand(True)
        stage.set_valign(Gtk.Align.FILL)
        stage.set_margin_top(26)
        stage.set_margin_bottom(14)
        stage.set_margin_start(self._prev_pad)
        stage.set_margin_end(self._prev_pad)

        screen = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        screen.get_style_context().add_class("screen")
        # 16:9 stage, sized to the same frame the decoder is capped at (see
        # _measure_panel) so the black screen never jumps size when a clip is
        # selected and a real frame arrives.
        screen.set_size_request(self._prev_w, self._prev_h)
        # ...but that size must not become the WHOLE WINDOW's floor. A Gtk.Image
        # cannot shrink below its pixbuf and this Box cannot shrink below its
        # request, so a screen big enough to look right on a desktop panel used
        # to stop the workspace shrinking at all: the window then overflowed a
        # smaller panel and the Properties column and the bottom of the timeline
        # were unreachable. A scroller that propagates its child's NATURAL size
        # gives us both — the screen still asks for its full size when there is
        # room, but it no longer sets the minimum. EXTERNAL keeps scrollbars off
        # the picture, and centring the scroller (rather than filling) keeps the
        # stage the same fixed, centred 16:9 rectangle it has always been.
        holder = Gtk.ScrolledWindow()
        holder.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.EXTERNAL)
        holder.set_propagate_natural_width(True)
        holder.set_propagate_natural_height(True)
        holder.get_style_context().add_class("prevframe")
        holder.set_hexpand(True)
        holder.set_vexpand(True)
        holder.set_valign(Gtk.Align.CENTER)
        holder.set_halign(Gtk.Align.CENTER)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        inner.set_valign(Gtk.Align.CENTER)
        inner.set_vexpand(True)
        # THE VIDEO SURFACE. gtksink's own widget, shown only while a moving
        # clip is actually playing; the still Gtk.Image below keeps every other
        # job (the selected-clip frame, scrubbing, stills and title cards). Both
        # live in the same box and only one is ever visible, so the 16:9 stage
        # never changes size when playback starts — which is why this is packed
        # here rather than swapped in.
        self._player = nbvideo.Playback(on_eos=self._on_clip_eos)
        if self._player.widget is not None:
            self._player.widget.set_no_show_all(True)
            self._player.widget.set_size_request(self._prev_w, self._prev_h)
            self._player.widget.set_halign(Gtk.Align.CENTER)
            self._player.widget.set_valign(Gtk.Align.CENTER)
            inner.pack_start(self._player.widget, False, False, 0)

        # the real decoded frame — hidden until the engine produces one; it
        # never fakes a frame, it only ever shows pixels ffmpeg/GStreamer gave us
        self._prev_image = Gtk.Image()
        self._prev_image.set_halign(Gtk.Align.CENTER)
        self._prev_image.set_valign(Gtk.Align.CENTER)
        self._prev_image.set_no_show_all(True)
        inner.pack_start(self._prev_image, False, False, 0)
        # the honest placeholder glyph + line, shown whenever there is no frame
        self._prev_glyph = nbicons.image("play", 44, "#8A857A")
        self._prev_glyph.set_halign(Gtk.Align.CENTER)
        inner.pack_start(self._prev_glyph, False, False, 0)
        self._prev_label = Gtk.Label(label=_t("Nothing to preview"))
        self._prev_label.get_style_context().add_class("noprev")
        inner.pack_start(self._prev_label, False, False, 0)
        # a metadata caption for the selected clip (title/kind/duration). Hidden
        # until a clip is selected — it never fakes a rendered video frame.
        self._prev_sub = Gtk.Label(label="")
        self._prev_sub.get_style_context().add_class("prevsub")
        self._prev_sub.set_no_show_all(True)
        inner.pack_start(self._prev_sub, False, False, 0)
        screen.pack_start(inner, True, True, 0)
        holder.add(screen)               # auto-wraps the screen in a viewport
        stage.pack_start(holder, True, True, 0)
        box.pack_start(stage, True, True, 0)

        # transport controls
        ctl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        ctl.set_halign(Gtk.Align.CENTER)
        ctl.set_margin_bottom(18)
        ctl.set_margin_top(6)
        prev_w, _ = self._round("prev", 36, 15, self._on_prev, "Previous clip")
        play_w, self._play_img = self._round("play", 46, 18, self._on_play, "Play")
        next_w, _ = self._round("next", 36, 15, self._on_next, "Next clip")
        self._play_w = play_w
        ctl.pack_start(prev_w, False, False, 0)
        ctl.pack_start(play_w, False, False, 0)
        ctl.pack_start(next_w, False, False, 0)
        self._tc = Gtk.Label(label="00:00:00 / 00:00:00")
        self._tc.get_style_context().add_class("timecode")
        self._tc.set_margin_start(14)
        ctl.pack_start(self._tc, False, False, 0)
        box.pack_start(ctl, False, False, 0)
        return box

    def _round(self, icon, size, isize, cb=None, tooltip=None):
        b = Gtk.Box() if cb is None else Gtk.Button()
        if cb is not None:
            b.set_relief(Gtk.ReliefStyle.NONE)
        b.get_style_context().add_class("roundbtn")
        b.set_size_request(size, size)
        b.set_halign(Gtk.Align.CENTER)
        b.set_valign(Gtk.Align.CENTER)
        img = nbicons.image(icon, isize, FAINT)
        img.set_halign(Gtk.Align.CENTER)
        img.set_valign(Gtk.Align.CENTER)
        if cb is None:
            img.set_hexpand(True)
            img.set_vexpand(True)
            b.pack_start(img, True, True, 0)
        else:
            b.add(img)
        if cb is None:
            if tooltip:
                b.set_tooltip_text(tooltip)
            return b, img
        b.connect("clicked", cb)
        if tooltip:
            b.set_tooltip_text(tooltip)
        return b, img

    def _update_preview(self):
        clip = self._sel_clip()
        if clip is None:
            # translated here, not at construction: the stage note is only
            # ever applied with a bare set_text() as the selection changes, so
            # raw English snapped the preview back to English mid-session
            self._show_placeholder(_t("Background music") if self._sel_music
                                   else _t("Nothing to preview"))
            self._prev_sub.hide()
            return
        kind = clip.get("kind", "video")
        if kind == "title":
            self._prev_sub.set_text(
                _t("Title card  ·  %s") % self._fmt_hms(self._clip_dur(clip)))
            self._prev_sub.set_no_show_all(False)
            self._prev_sub.show()
            # the REAL card, drawn by the same cairo routine the export burns in,
            # rather than the card's words set in the app's own type on the dark
            # stage — which is not what the film will show
            pb = self._card_preview(clip)
            if pb is not None:
                self._show_frame(pb)
            else:
                self._show_placeholder(clip.get("cardtext") or _t("Title card"))
            return
        media = self._clip_media(clip)
        name = (clip.get("title") or (media["name"] if media else _t("Clip")))
        self._prev_sub.set_text("%s  ·  %s" % (
            _t(KIND_LABEL.get(kind, "Clip")),
            self._fmt_hms(self._clip_dur(clip))))
        self._prev_sub.set_no_show_all(False)
        self._prev_sub.show()

        # a real decoded frame, cached per media path (video frames arrive
        # asynchronously — the poll paints them in when the decode finishes).
        pixbuf = self._request_frame(clip)
        if pixbuf is not None:
            self._show_frame(pixbuf)
            return
        # No frame yet — show the honest name/placeholder, never a fake frame.
        if kind == "audio":
            note = name          # audio has no visual; show its title
        elif not (media and os.path.isfile(media.get("path", ""))):
            note = name          # file went missing since import
        elif not self._ffmpeg_ok():
            note = _t("Preview isn’t available")
        else:
            note = name          # a video frame is decoding, or decode failed
        self._show_placeholder(note)

    def _card_preview(self, clip, w=None, h=None):
        """A title card as it will actually be exported, scaled for the screen.

        Rendered straight to a pixbuf: it is cairo drawing, so there is nothing
        to decode and no subprocess to wait on. Cached per card so retyping a
        title does not redraw on every keystroke more than once."""
        if not PIXBUF_OK:
            return None
        w = w or self._prev_w
        h = h or self._prev_h
        # w/h stay LOGICAL for the caller and the cache identity; the raster is
        # built at device resolution below. Both consumers hand the result to
        # Gtk.Image as a device-scaled surface, so the card occupies the same
        # logical space and only gains detail. sf is in the key because the same
        # card at the same logical size is a different raster on a 2x screen.
        sf = nbicons.scale_factor()
        key = ("title", clip.get("cardtext", ""), clip.get("cardsub", ""),
               w, h, sf, self._out_w, self._out_h)
        cache = getattr(self, "_cardpv_cache", None)
        if cache is None:
            cache = self._cardpv_cache = {}
        if key in cache:
            return cache[key] or None
        png = None
        try:
            self._exp_tmp_imgs = getattr(self, "_exp_tmp_imgs", None) or []
            before = len(self._exp_tmp_imgs)
            png = self._render_card_png(clip.get("cardtext", ""),
                                        clip.get("cardsub", ""))
            del self._exp_tmp_imgs[before:]      # ours to delete, not the export's
            pb = (GdkPixbuf.Pixbuf.new_from_file_at_scale(
                      png, w * sf, h * sf, True)
                  if png else None)
        except Exception:
            pb = None
        if png:
            try:
                os.remove(png)
            except OSError:
                pass
        if len(cache) > 24:
            cache.clear()
        cache[key] = pb if pb is not None else False
        return pb

    def _show_frame(self, pixbuf):
        """Put a real decoded frame on the stage, in place of the placeholder."""
        try:
            # Device-scaled surface, not a raw pixbuf: see _decode_image.
            nbicons.set_image_pixbuf(self._prev_image, pixbuf)
            self._prev_image.set_no_show_all(False)
            self._prev_image.show()
            self._prev_glyph.hide()
            self._prev_label.hide()
        except Exception:
            pass

    def _show_placeholder(self, text):
        """Reveal the neutral glyph + line and hide the decoded-frame image."""
        try:
            self._prev_image.hide()
            self._prev_glyph.show()
            self._prev_label.set_text(text)
            self._prev_label.show()
        except Exception:
            pass

    # ---------------- preview frame engine (ffmpeg / GStreamer) ----------
    def _ffmpeg_path(self):
        """The ffmpeg CLI, or None. Probed live so a mid-session install is
        picked up and the host (no ffmpeg) degrades cleanly."""
        try:
            return shutil.which("ffmpeg")
        except Exception:
            return None

    def _ffmpeg_ok(self):
        return self._ffmpeg_path() is not None

    def _request_frame(self, clip):
        """Return a cached preview pixbuf for `clip`, or None.

        Stills decode in-process (fast). Video frames decode ASYNCHRONOUSLY —
        an ffmpeg subprocess polled by GLib.timeout — so a slow or large clip
        never blocks the GTK main loop: this returns None immediately and the
        poll paints the finished frame onto the stage. Results (including a
        failed decode, cached as False) are memoised per FRAME KEY — the media
        path plus the clip's trim-in point — so re-selecting a clip is instant,
        a bad file is not retried forever, and two clips cut from one file
        (which is exactly what Split makes) keep their own pictures."""
        if not PIXBUF_OK:
            return None
        media = self._clip_media(clip)
        if not media:
            return None
        path = media.get("path")
        kind = media.get("kind")
        if not path or not os.path.isfile(path):
            return None
        key = self._frame_key(clip)
        cache = self._frame_cache
        if key in cache:
            return cache[key] or None
        # Stills only decode in-process when there is nothing to composite: the
        # moment a clip carries an effect or a caption the picture has to come
        # through the export's own chain, or the stage shows something the file
        # will not contain.
        if kind == "image" and not self._has_look(clip):
            pb = self._decode_image(path)
            if pb is not None:
                cache[key] = pb
                return pb
            # GdkPixbuf cannot load every picture Import accepts (.webp has no
            # pixbuf loader on this image), and those stills used to sit on a
            # blank placeholder forever even though they export fine. Fall back
            # to the same ffmpeg decoder video frames come from.
        if kind in ("image", "video") and self._ffmpeg_ok():
            # a little past the in-point, so a leading black frame is skipped
            self._pv_start(key, clip)
            return None
        cache[key] = False
        return None

    @staticmethod
    def _has_look(clip):
        """True when a clip's picture is not just its source frame — it carries a
        visual effect or a caption that the export will burn in."""
        return bool(clip.get("title")
                    or (clip.get("effect", "none") or "none") != "none")

    def _clip_start(self, clip):
        try:
            return float(clip.get("start", 0.0) or 0.0)
        except Exception:
            return 0.0

    def _frame_key(self, clip):
        """What a decoded frame belongs to: the file, the point in it, and every
        edit that changes the picture.

        Split hands both halves the same media and different in-points; keyed
        on the path alone they shared one cached frame, so the stage and both
        storyboard cards showed the same picture. The visual effect and the
        caption are in the key for the same reason: the preview renders through
        the export's own chain, so a frame decoded before an effect was applied
        is a different picture from the one after."""
        media = self._clip_media(clip) or {}
        return (media.get("path"), round(self._clip_start(clip), 3),
                clip.get("effect", "none") or "none", clip.get("title", "") or "")

    def _clip_seek(self, clip):
        """Where in the source to grab this clip's picture: just past the
        in-point THE EXPORT WILL USE, so a leading black frame is skipped and
        the preview shows the frame the finished film opens this clip on.

        Going through _render_start (rather than the raw "start" field) is what
        keeps it inside the source. The Trim spinner's range runs to the whole
        length of the media, so trimming a six-second clip to six seconds — or
        anywhere in the last half-second — asked ffmpeg for a frame PAST the
        end. ffmpeg decodes nothing, exits 0, and leaves a zero-byte PNG, so the
        stage and the storyboard card went blank with nothing said, and stayed
        blank (the frame cache keys on the in-point). The export was fine the
        whole time, because it clamps. Now both clamp the same way, and the
        preview is once again a picture of what will be rendered."""
        dur = self._clip_dur(clip)
        speed = float(clip.get("speed", 1.0) or 1.0)
        t = self._render_start(clip, dur, speed) + min(0.5, dur / 4.0)
        srcdur = self._clip_srcdur(clip)
        if srcdur:
            # the nudge past the in-point must not walk off the end either (a
            # source shorter than half a second)
            t = min(t, max(0.0, srcdur - 0.05))
        return t

    def _invalidate_clip_frame(self, clip):
        """Drop a clip's cached preview frame (after a trim) so it re-decodes
        at the new in-point on the next preview refresh."""
        key = self._frame_key(clip)
        self._frame_cache.pop(key, None)
        self._card_thumbs.pop(key, None)
        if getattr(self, "_pv_path", None) is not None:
            self._pv_teardown()

    # ---- storyboard card thumbnails ---------------------------------------
    # A storyboard of file names is not a storyboard. Each card carries a real
    # picture of its clip: stills decode in-process, video frames come from the
    # SAME async ffmpeg decoder the preview uses, queued one at a time so a
    # twelve-clip project never spawns twelve subprocesses at once.
    CARD_W, CARD_H = 126, 71
    # a card is its picture plus the number / name / length / badge lines
    STORY_H = 180

    def _card_w(self):
        """Storyboard card width. Zooming out no longer shrinks a card below
        the picture it carries, which is the point of the card."""
        return max(int(150 * self._zoom), self.CARD_W + 14)

    def _card_pixbuf(self, clip):
        """The card-sized frame for `clip`, or None (not decoded yet, or a
        title card, which draws its own text instead)."""
        if not PIXBUF_OK:
            return None
        media = self._clip_media(clip)
        if not media:
            return None
        path = media.get("path")
        if not path or not os.path.isfile(path):
            return None
        key = self._frame_key(clip)
        hit = self._card_thumbs.get(key)
        if hit is not None:
            return hit or None
        kind = media.get("kind")
        if kind == "audio":
            return None                      # audio has no picture to show
        # A still with an effect or a caption on it has to come through the
        # export's chain, same as the stage frame does — a card showing the raw
        # photo when the film will show a sepia one with a caption over it is a
        # card telling the user the wrong thing.
        if kind == "image" and not self._has_look(clip):
            try:
                _sf = nbicons.scale_factor()
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    path, self.CARD_W * _sf, self.CARD_H * _sf, True)
            except Exception:
                pb = None
            if pb is not None:
                self._card_thumbs[key] = pb
                return pb
            # a still GdkPixbuf can't read (.webp) falls through to the shared
            # ffmpeg frame decoder below, same as _request_frame does
        frame = self._frame_cache.get(key)
        if frame:
            pb = self._scale_to_card(frame)
            self._card_thumbs[key] = pb if pb is not None else False
            return pb
        if frame is None and self._ffmpeg_ok():
            self._queue_card_frame(clip, key, path)
        return None

    def _scale_to_card(self, pb):
        """Fit a frame to a storyboard card, at the SCREEN's pixel density.

        CARD_W/CARD_H stay logical -- they are layout numbers -- but the pixbuf
        is built at card size * device scale, so a 2x panel gets a real 252x142
        thumbnail rather than a 126x71 one the compositor stretches. Every
        consumer of this pixbuf must hand it to Gtk.Image as a device-scaled
        SURFACE (nbicons.set_image_pixbuf); handing it over as a plain pixbuf
        would place those extra pixels as logical ones and double the card."""
        try:
            w, h = pb.get_width(), pb.get_height()
            if w <= 0 or h <= 0:
                return None
            sf = nbicons.scale_factor()
            k = min((self.CARD_W * sf) / float(w), (self.CARD_H * sf) / float(h))
            return pb.scale_simple(max(1, int(w * k)), max(1, int(h * k)),
                                   GdkPixbuf.InterpType.BILINEAR)
        except Exception:
            return None

    def _queue_card_frame(self, clip, key, path):
        """Ask for a video clip's frame in the background, behind whatever the
        preview is decoding."""
        if key != self._pv_key and key not in self._pv_queue:
            self._pv_queue.append(key)
            self._pv_seek = getattr(self, "_pv_seek", {})
            self._pv_seek[key] = dict(clip)
        # always pump, even for a path already queued: a preview decode that
        # was cancelled mid-flight (a trim, a new selection) leaves the queue
        # with no running job, and the next repaint is what restarts it.
        self._pv_pump()

    def _pv_pump(self):
        """Start the next queued card decode, if nothing else is decoding."""
        while self._pv_queue and self._pv_proc is None:
            key = self._pv_queue.pop(0)
            clip = getattr(self, "_pv_seek", {}).get(key)
            if key in self._frame_cache or clip is None:
                continue
            media = self._clip_media(clip)
            if not media or not os.path.isfile(media.get("path", "")):
                continue
            self._pv_start(key, clip)
            return

    def _paint_card_thumbs(self, key):
        """Fill in the storyboard cards waiting on this frame, in place."""
        pb = self._card_thumbs.get(key)
        if not pb:
            return
        for img in self._card_imgs.get(key, ()):
            try:
                # The pixbuf is at DEVICE resolution (see _scale_to_card), so it
                # goes in as a surface carrying the scale -- set_from_pixbuf
                # would treat those pixels as logical and draw a double-size
                # card, pushing the storyboard past the window.
                nbicons.set_image_pixbuf(img, pb)
                img.show()
            except Exception:
                pass

    def _decode_image(self, path):
        """Load a still, scaled to fit the stage (aspect preserved). The cap is
        the panel-derived frame size, not the desktop PREV_W/PREV_H: a Gtk.Image
        cannot shrink below its pixbuf, so an oversized frame would push the
        whole window past a small panel (see _measure_panel)."""
        try:
            # * device scale, so the stage shows the still at panel resolution.
            # The docstring's warning above is exactly why every consumer must
            # use nbicons.set_image_pixbuf: a Gtk.Image cannot shrink below its
            # pixbuf, so handing this over as a plain pixbuf on a 2x screen
            # would double the stage's minimum size and push the window off a
            # small panel. As a device-scaled surface the LOGICAL size is
            # unchanged and only the detail goes up.
            _sf = nbicons.scale_factor()
            return GdkPixbuf.Pixbuf.new_from_file_at_scale(
                path, self._prev_w * _sf, self._prev_h * _sf, True)
        except Exception:
            return None

    # ---- asynchronous video-frame decode (ffmpeg -> PNG -> pixbuf) ----------
    # The preview decodes through the EXPORT's own chain, at preview scale: the
    # same letterbox, the same visual effect, the same burned-in caption. It used
    # to be a bare `scale`, so the stage showed the raw source frame and the file
    # came out looking different — a preview that does not answer the one question
    # it is for.
    def _pv_frame_cmd(self, ff, clip, path, t, tmp):
        """argv decoding one preview frame of `clip` exactly as it will look."""
        PW, PH = self._prev_w, self._prev_h
        base = ("scale=%d:%d:force_original_aspect_ratio=decrease,"
                "pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=%s"
                % (PW, PH, PW, PH, EXPORT_BG))
        ef = EFFECT_VF.get(clip.get("effect", "none"), "")
        if ef:
            base += "," + ef
        args = [ff, "-nostdin", "-y", "-ss", "%.3f" % max(0.0, t), "-i", path]
        cap = clip.get("title", "") if clip.get("kind") in ("video", "image") \
            else ""
        cpng = self._render_caption_png(cap) if cap else None
        if cpng:
            # a preview scratch file, NOT an export one: _exp_cleanup_tmp runs on
            # its own schedule and would either delete this mid-decode or never
            # see it at all
            try:
                self._exp_tmp_imgs.remove(cpng)
            except (ValueError, AttributeError):
                pass
            self._pv_imgs = getattr(self, "_pv_imgs", [])
            self._pv_imgs.append(cpng)
            args += ["-i", cpng]
            # format=rgba is not optional: scaling the caption PNG lets sws
            # negotiate an alpha-less pixel format, and the transparent part of
            # the overlay then composites as OPAQUE BLACK — every captioned
            # clip's preview and storyboard card came out a black rectangle.
            args += ["-filter_complex",
                     "[0:v]%s[b];[1:v]scale=%d:%d,format=rgba[c];"
                     "[b][c]overlay=0:0[v]" % (base, PW, PH), "-map", "[v]"]
        else:
            args += ["-vf", base]
        args += ["-frames:v", "1", "-f", "image2", tmp]
        return args

    def _pv_start(self, key, clip, cache=True, seek=None):
        """Start (or refresh) the single background frame decode. Only one runs
        at a time — selecting a different clip cancels the in-flight one, so a
        rapid walk through the bin never stacks up subprocesses.

        `cache=False` is for the frames playback asks for as it runs: they are
        painted and dropped, because keeping one per second of a long film would
        fill memory with pictures nobody will look at twice."""
        if getattr(self, "_pv_key", None) == key and self._pv_proc is not None:
            return   # already decoding exactly this frame
        self._pv_teardown()
        ff = self._ffmpeg_path()
        media = self._clip_media(clip)
        path = media.get("path") if media else None
        if not ff or not path:
            return
        if seek is not None:
            t = max(0.0, float(seek))
        else:
            t = 0.0 if clip.get("kind") == "image" else self._clip_seek(clip)
        self._pv_cache = bool(cache)
        try:
            fd, tmp = tempfile.mkstemp(prefix="nbvid-frame-", suffix=".png")
            os.close(fd)
        except Exception:
            return
        # the caption PNG this frame may need is a scratch file of its own; keep
        # it on the same list the export cleans up
        self._exp_tmp_imgs = getattr(self, "_exp_tmp_imgs", None) or []
        cmd = self._pv_frame_cmd(ff, clip, path, t, tmp)
        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
        except Exception:
            try:
                os.remove(tmp)
            except Exception:
                pass
            return
        self._pv_proc = proc
        self._pv_tmp = tmp
        self._pv_path = path
        self._pv_key = key
        self._pv_poll_id = GLib.timeout_add(120, self._pv_poll)

    def _pv_poll(self):
        proc = self._pv_proc
        if proc is None:
            self._pv_poll_id = 0
            return False
        if proc.poll() is None:
            return True          # still decoding — keep polling
        self._pv_poll_id = 0
        tmp, key = self._pv_tmp, self._pv_key
        pixbuf = None
        try:
            if (proc.returncode == 0 and tmp and os.path.exists(tmp)
                    and os.path.getsize(tmp) > 0):
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(tmp)
        except Exception:
            pixbuf = None
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
        for p in getattr(self, "_pv_imgs", []) or []:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
        self._pv_imgs = []
        self._pv_proc = None
        self._pv_tmp = None
        self._pv_path = None
        self._pv_key = None
        cached = getattr(self, "_pv_cache", True)
        if not cached:
            # a playback frame: paint it if we are still on the shot it came
            # from, then let it go
            if pixbuf is not None and self._playing and isinstance(key, tuple) \
                    and len(key) == 3 and key[1] == self._sel_cell:
                try:
                    self._show_frame(pixbuf)
                except Exception:
                    pass
            self._pv_pump()
            return False
        if key is not None:
            self._frame_cache[key] = pixbuf if pixbuf is not None else False
            # the same frame, scaled down, is this clip's storyboard card
            card = self._scale_to_card(pixbuf) if pixbuf is not None else None
            self._card_thumbs[key] = card if card is not None else False
            self._paint_card_thumbs(key)
        # only paint it if the clip we decoded for is still the selected one
        clip = self._sel_clip()
        if pixbuf is not None and clip and self._frame_key(clip) == key:
            try:
                self._show_frame(pixbuf)
            except Exception:
                pass
        self._pv_pump()          # move on to the next card waiting for a frame
        return False

    def _pv_teardown(self):
        """Stop any in-flight preview decode and drop its scratch PNG. Safe to
        call repeatedly (new selection, close, destroy)."""
        pid = getattr(self, "_pv_poll_id", 0)
        if pid:
            try:
                GLib.source_remove(pid)
            except Exception:
                pass
            self._pv_poll_id = 0
        proc = getattr(self, "_pv_proc", None)
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
            # _pv_pump takes a key OFF the queue before starting it, so a card
            # decode cancelled here (by the stage wanting a different frame) used
            # to be dropped for good and that card stayed blank until something
            # else repainted the storyboard. Put it back.
            key = getattr(self, "_pv_key", None)
            if (key is not None and getattr(self, "_pv_cache", True)
                    and key not in self._frame_cache
                    and key not in self._pv_queue
                    and key in getattr(self, "_pv_seek", {})):
                self._pv_queue.append(key)
        self._pv_proc = None
        tmp = getattr(self, "_pv_tmp", None)
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
        self._pv_tmp = None
        self._pv_path = None
        self._pv_key = None
        for p in getattr(self, "_pv_imgs", []) or []:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
        self._pv_imgs = []

    # ================= properties =================
    # Which Properties sections apply to each clip kind — everything else is
    # hidden so the panel only ever offers edits that mean something.
    PROP_SHOW = {
        "video": {"caption", "trim", "length", "speed", "effect", "volume",
                  "afade", "vfade", "transition", "arrange"},
        "image": {"caption", "length", "effect", "kenburns", "vfade",
                  "transition", "arrange"},
        "audio": {"trim", "length", "volume", "afade", "transition", "arrange"},
        "title": {"card", "length", "vfade", "transition", "arrange"},
    }

    def _properties(self):
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.set_size_request(self._prop_w, -1)
        col.get_style_context().add_class("propcol")

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.get_style_context().add_class("propscroll")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.pack_start(scroll, True, True, 0)
        scroll.add(box)

        head = Gtk.Label(label=_t("PROPERTIES"), xalign=0)
        head.get_style_context().add_class("prophead")
        box.pack_start(head, False, False, 0)

        # shown when nothing is selected
        self._prop_hint = Gtk.Label(label=_t("No clip selected"))
        self._prop_hint.set_line_wrap(True)
        self._prop_hint.set_xalign(0)
        self._prop_hint.set_max_width_chars(30)
        self._prop_hint.get_style_context().add_class("prophint")
        self._prop_hint.set_no_show_all(True)
        box.pack_start(self._prop_hint, False, False, 0)

        # ---- the per-clip editor ----
        ed = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        ed.get_style_context().add_class("propeditor")
        ed.set_no_show_all(True)
        self._prop_editor = ed
        self._prop_rows = {}

        def section(label, widgets, key, top=13):
            sec = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            # _t here, not at each call site: every field heading in this panel
            # was raw English, so Properties — the pane a person spends the whole
            # session in — was untranslated in all sixteen other languages
            lab = Gtk.Label(label=_t(label), xalign=0)
            lab.get_style_context().add_class("propfieldlabel")
            lab.set_margin_top(top)
            sec.pack_start(lab, False, False, 0)
            for w in widgets:
                sec.pack_start(w, False, False, 0)
            self._prop_rows[key] = sec
            ed.pack_start(sec, False, False, 0)
            return sec

        self._prop_name = Gtk.Label(label="", xalign=0)
        self._prop_name.get_style_context().add_class("propname")
        self._prop_name.set_ellipsize(Pango.EllipsizeMode.END)
        self._prop_name.set_max_width_chars(26)
        ed.pack_start(self._prop_name, False, False, 0)

        # title card text (kind == title)
        self._prop_cardtext = self._entry("Title text…", self._on_cardtext,
                                           self._on_card_focus)
        self._prop_cardsub = self._entry("Subtitle (optional)…",
                                          self._on_cardsub, self._on_card_focus)
        section("TITLE CARD", [self._prop_cardtext, self._prop_cardsub], "card")

        # caption overlay
        self._prop_title = self._entry("Add a caption…", self._on_caption,
                                        self._on_caption_focus)
        section("CAPTION", [self._prop_title], "caption")

        # trim in-point. Half-second steps, not whole seconds: the in-point is
        # where a shot starts, and a whole second is a long way past the moment
        # you meant. The model has always held a float here; only the control
        # was rounding it.
        self._prop_trim = Gtk.SpinButton.new_with_range(0, 36000, 0.5)
        self._prop_trim.set_digits(1)
        self._prop_trim.get_style_context().add_class("propentry")
        self._prop_trim.set_numeric(True)
        self._prop_trim.connect("value-changed", self._on_trim_changed)
        self._prop_trim_hint = Gtk.Label(label="", xalign=0)
        self._prop_trim_hint.get_style_context().add_class("prophint")
        section("TRIM START (SECONDS)",
                [self._prop_trim, self._prop_trim_hint], "trim")

        # length
        self._prop_dur = Gtk.SpinButton.new_with_range(1, 3600, 1)
        self._prop_dur.get_style_context().add_class("propentry")
        self._prop_dur.set_numeric(True)
        self._prop_dur.connect("value-changed", self._on_dur_changed)
        section("LENGTH (SECONDS)", [self._prop_dur], "length")

        # speed (segmented)
        speedbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        speedbox.get_style_context().add_class("seg")
        self._prop_speed_btns = {}
        for val, lab in SPEEDS:
            b = Gtk.Button(label=lab)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("segbtn")
            b.connect("clicked", lambda _w, v=val: self._on_speed(v))
            self._prop_speed_btns[val] = b
            speedbox.pack_start(b, True, True, 0)
        section("SPEED", [speedbox], "speed")

        # visual effect
        self._prop_effect = Gtk.ComboBoxText()
        self._prop_effect.get_style_context().add_class("propcombo")
        for key, name in EFFECTS:
            self._prop_effect.append(key, _t(name))
        self._prop_effect.connect("changed", self._on_effect_changed)
        section("VISUAL EFFECT", [self._prop_effect], "effect")

        # ken burns (stills)
        self._prop_kb = Gtk.ComboBoxText()
        self._prop_kb.get_style_context().add_class("propcombo")
        for key, name in KENBURNS:
            self._prop_kb.append(key, _t(name))
        self._prop_kb.connect("changed", self._on_kb_changed)
        section("PAN & ZOOM", [self._prop_kb], "kenburns")

        # volume + mute
        self._prop_vol = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 200, 5)
        self._prop_vol.set_draw_value(False)
        self._prop_vol.set_hexpand(True)
        self._prop_vol.add_mark(100, Gtk.PositionType.BOTTOM, None)
        self._prop_vol.connect("value-changed", self._on_vol_changed)
        self._prop_mute = Gtk.CheckButton(label=_t("Mute this clip"))
        self._prop_mute.get_style_context().add_class("propcheck")
        self._prop_mute.connect("toggled", self._on_mute_toggled)
        section("VOLUME", [self._prop_vol, self._prop_mute], "volume")

        # audio fade
        self._prop_afade = Gtk.CheckButton(label=_t("Fade audio in / out"))
        self._prop_afade.get_style_context().add_class("propcheck")
        self._prop_afade.connect("toggled", self._on_afade_toggled)
        section("AUDIO FADE", [self._prop_afade], "afade")

        # video fade
        self._prop_vfade = Gtk.CheckButton(label=_t("Fade from / to black"))
        self._prop_vfade.get_style_context().add_class("propcheck")
        self._prop_vfade.connect("toggled", self._on_vfade_toggled)
        section("VIDEO FADE", [self._prop_vfade], "vfade")

        # transition
        self._prop_trans = Gtk.Label(label=_t("None"), xalign=0)
        self._prop_trans.get_style_context().add_class("propval")
        section("TRANSITION (LEAD-IN)", [self._prop_trans], "transition")

        # arrange: move + remove
        arrange = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        arrange.set_margin_top(4)
        mvl = Gtk.Button(label=_t("◀ Move"))
        mvl.set_relief(Gtk.ReliefStyle.NONE)
        mvl.get_style_context().add_class("propmove")
        mvl.connect("clicked", lambda *_: self._move_clip(-1))
        mvr = Gtk.Button(label=_t("Move ▶"))
        mvr.set_relief(Gtk.ReliefStyle.NONE)
        mvr.get_style_context().add_class("propmove")
        mvr.connect("clicked", lambda *_: self._move_clip(1))
        rm = Gtk.Button(label=_t("Remove"))
        rm.set_relief(Gtk.ReliefStyle.NONE)
        rm.get_style_context().add_class("propremove")
        rm.set_halign(Gtk.Align.START)
        rm.set_margin_top(8)
        rm.connect("clicked", lambda *_: self._delete_clip_guarded())
        arrange.pack_start(mvl, False, False, 0)
        arrange.pack_start(mvr, False, False, 0)
        # Remove sits on its own line beneath the two Move buttons: all three
        # side by side needed 226px, which forced this whole column wider than
        # a small panel could spare, and it keeps the destructive action apart
        # from the harmless ones (matching the music editor's Remove music).
        section("ARRANGE", [arrange, rm], "arrange")
        box.pack_start(ed, False, False, 0)

        # ---- the background-music editor ----
        med = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        med.get_style_context().add_class("propeditor")
        med.set_no_show_all(True)
        self._music_editor = med
        mh = Gtk.Label(label=_t("BACKGROUND MUSIC"), xalign=0)
        mh.get_style_context().add_class("propfieldlabel")
        med.pack_start(mh, False, False, 0)
        self._mus_name = Gtk.Label(label="", xalign=0)
        self._mus_name.get_style_context().add_class("propname")
        self._mus_name.set_ellipsize(Pango.EllipsizeMode.END)
        self._mus_name.set_max_width_chars(26)
        med.pack_start(self._mus_name, False, False, 0)
        mvl2 = Gtk.Label(label=_t("VOLUME"), xalign=0)
        mvl2.get_style_context().add_class("propfieldlabel")
        mvl2.set_margin_top(12)
        med.pack_start(mvl2, False, False, 0)
        self._mus_vol = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 150, 5)
        self._mus_vol.set_draw_value(False)
        self._mus_vol.add_mark(60, Gtk.PositionType.BOTTOM, None)
        self._mus_vol.connect("value-changed", self._on_mus_vol)
        med.pack_start(self._mus_vol, False, False, 0)
        self._mus_fadein = Gtk.CheckButton(label=_t("Fade in"))
        self._mus_fadein.get_style_context().add_class("propcheck")
        self._mus_fadein.connect("toggled", self._on_mus_fade)
        self._mus_fadeout = Gtk.CheckButton(label=_t("Fade out"))
        self._mus_fadeout.get_style_context().add_class("propcheck")
        self._mus_fadeout.connect("toggled", self._on_mus_fade)
        med.pack_start(self._mus_fadein, False, False, 0)
        med.pack_start(self._mus_fadeout, False, False, 0)
        rmm = Gtk.Button(label=_t("Remove music"))
        rmm.set_relief(Gtk.ReliefStyle.NONE)
        rmm.get_style_context().add_class("propremove")
        rmm.set_halign(Gtk.Align.START)
        rmm.set_margin_top(14)
        rmm.connect("clicked", lambda *_: self._remove_music())
        med.pack_start(rmm, False, False, 0)
        box.pack_start(med, False, False, 0)

        # project summary (Duration + Clips update live). "Format" used to sit
        # here too, but it is fixed, unchangeable, and already stated in the
        # Export dialog where it actually matters — as a pinned row it only cost
        # the clip editor height.
        table = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        table.get_style_context().add_class("proptable")
        self._prop_vals = {}
        for k, v in (("Project", "Untitled"),
                     ("Duration", "00:00:00"), ("Clips", "0")):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            row.get_style_context().add_class("proprow")
            kl = Gtk.Label(label=k, xalign=0)
            kl.get_style_context().add_class("propkey")
            row.pack_start(kl, False, False, 0)
            vl = Gtk.Label(label=v, xalign=1)
            vl.get_style_context().add_class("propval")
            row.pack_end(vl, False, False, 0)
            self._prop_vals[k] = vl
            table.pack_start(row, False, False, 0)
        # The project summary is PINNED to the bottom of the column, outside the
        # scroller. It used to sit under the clip editor, which on a small panel
        # put the movie's own length and clip count three screenfuls down — the
        # two numbers a person checks most while editing, and the only ones that
        # are not about the selected clip. Now they are always on screen, and
        # the scroller only has to carry the clip editor.
        col.pack_start(table, False, False, 0)
        return col

    def _entry(self, placeholder, on_change, on_focus_out):
        e = Gtk.Entry()
        e.get_style_context().add_class("propentry")
        e.set_placeholder_text(placeholder)
        e.connect("changed", on_change)
        e.connect("activate", lambda *_: self._save_project())
        e.connect("focus-out-event", on_focus_out)
        return e

    def _cur_clip(self):
        k = self._sel_cell
        if k is None or not (0 <= k < len(self.clips)):
            return None
        return self.clips[k]

    def _apply_prop_visibility(self, kind):
        show = self.PROP_SHOW.get(kind, self.PROP_SHOW["video"])
        for key, row in self._prop_rows.items():
            if key in show:
                row.show_all()
            else:
                row.hide()

    def _update_prop_view(self, clip):
        # Route Properties between the hint, the per-clip editor and the music
        # editor. Everything starts no-show-all so the window's initial
        # show_all() leaves them managed here.
        if self._sel_music:
            self._music_editor.set_no_show_all(False)
            self._music_editor.show_all()
            self._prop_hint.hide()
            self._prop_editor.hide()
            return
        self._music_editor.hide()
        if clip is not None:
            self._prop_editor.set_no_show_all(False)
            self._prop_editor.show_all()
            self._apply_prop_visibility(clip.get("kind", "video"))
            self._prop_hint.hide()
        else:
            self._prop_hint.set_no_show_all(False)
            self._prop_hint.show()
            self._prop_editor.hide()

    def _load_props(self, clip):
        """Push a clip's real values into the editable fields (or the music
        editor / hint), then refresh the project table."""
        if self._sel_music:
            self._load_music_props()
            return
        self._suspend_prop = True
        try:
            if clip is not None:
                kind = clip.get("kind", "video")
                media = self._clip_media(clip)
                self._prop_name.set_text(
                    _t("Title card") if kind == "title"
                    else (media["name"] if media else _t("Clip")))
                self._prop_cardtext.set_text(clip.get("cardtext", "") or "")
                self._prop_cardsub.set_text(clip.get("cardsub", "") or "")
                self._prop_title.set_text(clip.get("title", "") or "")
                srcdur = self._clip_srcdur(clip)
                self._prop_trim.set_range(0, max(1, int(srcdur) if srcdur
                                                 else 36000))
                self._prop_trim.set_value(float(clip.get("start", 0.0) or 0.0))
                self._prop_trim_hint.set_text(
                    ("of %s" % self._fmt_hms(srcdur)) if srcdur else "")
                self._prop_dur.set_value(self._clip_dur(clip))
                sp = float(clip.get("speed", 1.0))
                for v, b in self._prop_speed_btns.items():
                    sc = b.get_style_context()
                    (sc.add_class if v == sp else sc.remove_class)("active")
                self._prop_effect.set_active_id(clip.get("effect", "none"))
                self._prop_kb.set_active_id(clip.get("kenburns", "none"))
                self._prop_vol.set_value(
                    max(0.0, min(200.0, float(clip.get("volume", 1.0)) * 100.0)))
                self._prop_mute.set_active(bool(clip.get("mute")))
                self._prop_afade.set_active(bool(clip.get("afade")))
                self._prop_vfade.set_active(bool(clip.get("vfade")))
                self._prop_trans.set_text(
                    _t(TRANS_NAME.get(clip.get("transition")) or "None"))
        finally:
            self._suspend_prop = False
        self._update_prop_view(clip)
        self._update_props_table()

    def _load_music_props(self):
        self._suspend_prop = True
        try:
            m = self.music or {}
            self._mus_name.set_text(m.get("name", "Music"))
            self._mus_vol.set_value(
                max(0.0, min(150.0, float(m.get("volume", 0.6)) * 100.0)))
            self._mus_fadein.set_active(bool(m.get("fadein", True)))
            self._mus_fadeout.set_active(bool(m.get("fadeout", True)))
        finally:
            self._suspend_prop = False
        self._music_editor.set_no_show_all(False)
        self._music_editor.show_all()
        self._prop_hint.hide()
        self._prop_editor.hide()
        self._update_props_table()

    def _update_props_table(self):
        total = self._total()
        self._prop_vals["Duration"].set_text(self._fmt_hms(total))
        self._prop_vals["Clips"].set_text(str(len(self.clips)))
        try:
            self._tc.set_text("00:00:00 / " + self._fmt_hms(total))
        except Exception:
            pass

    # ---- per-clip field handlers ----
    def _on_caption(self, entry):
        if self._suspend_prop:
            return
        c = self._cur_clip()
        if c is None:
            return
        self._push_undo("cap:%s" % self._sel_cell)
        c["title"] = entry.get_text()
        self._update_preview()

    def _on_caption_focus(self, _w, _e):
        if not self._suspend_prop:
            self._render_story()
            self._render_timeline()
            self._save_project()
        return False

    def _on_cardtext(self, entry):
        if self._suspend_prop:
            return
        c = self._cur_clip()
        if c is not None:
            self._push_undo("card:%s" % self._sel_cell)
            c["cardtext"] = entry.get_text()
            self._update_preview()

    def _on_cardsub(self, entry):
        if self._suspend_prop:
            return
        c = self._cur_clip()
        if c is not None:
            self._push_undo("cardsub:%s" % self._sel_cell)
            c["cardsub"] = entry.get_text()

    def _on_card_focus(self, _w, _e):
        if not self._suspend_prop:
            self._render_story()
            self._render_timeline()
            self._save_project()
        return False

    def _on_trim_changed(self, spin):
        if self._suspend_prop:
            return
        c = self._cur_clip()
        if c is None:
            return
        self._push_undo("trim:%s" % self._sel_cell)
        c["start"] = float(spin.get_value())
        # A trim-in point can't leave the clip claiming more timeline than the
        # source has after it, or the exported clip (-ss start -t duration) would
        # fall short of its slot — Movie Maker trims the in-point, shortening the
        # clip. Clamp the length to what remains.
        clamped = False
        m = self._max_clip_dur(c)
        if m is not None and int(round(self._clip_dur(c))) > m:
            c["duration"] = m
            clamped = True
        self._save_project()
        self._invalidate_clip_frame(c)
        self._relaunch_live_clip(c)
        if clamped:
            # show the shortened slot in the duration field + storyboard/timeline
            self._suspend_prop = True
            try:
                self._prop_dur.set_value(c["duration"])
            except Exception:
                pass
            self._suspend_prop = False
            self._render_story()
            self._render_timeline()
        self._update_preview()

    def _on_dur_changed(self, spin):
        if self._suspend_prop:
            return
        c = self._cur_clip()
        if c is None:
            return
        self._push_undo("len:%s" % self._sel_cell)
        d = int(spin.get_value())
        # can't stretch a clip past the source it has (after trim, at its speed)
        m = self._max_clip_dur(c)
        if m is not None and d > m:
            d = m
            self._suspend_prop = True
            try:
                spin.set_value(m)
            except Exception:
                pass
            self._suspend_prop = False
        c["duration"] = d
        self._save_project()
        self._render_story()
        self._render_timeline()
        self._update_props_table()
        self._update_preview()

    def _on_speed(self, val):
        c = self._cur_clip()
        if c is None or self._suspend_prop:
            return
        self._push_undo("speed:%s" % self._sel_cell)
        c["speed"] = float(val)
        for v, b in self._prop_speed_btns.items():
            sc = b.get_style_context()
            (sc.add_class if v == val else sc.remove_class)("active")
        # a faster clip drains its source quicker, so it may no longer fill its
        # slot — clamp the on-timeline length to what it can now cover
        m = self._max_clip_dur(c)
        if m is not None and int(round(self._clip_dur(c))) > m:
            c["duration"] = m
            self._suspend_prop = True
            try:
                self._prop_dur.set_value(m)
            except Exception:
                pass
            self._suspend_prop = False
        self._relaunch_live_clip(c)
        self._save_project()
        self._render_story()
        self._render_timeline()

    def _on_effect_changed(self, combo):
        if self._suspend_prop:
            return
        c = self._cur_clip()
        if c is None:
            return
        self._push_undo("fx:%s" % self._sel_cell)
        c["effect"] = combo.get_active_id() or "none"
        self._save_project()
        self._render_story()
        self._update_preview()      # the stage shows the effect, so re-decode

    def _on_kb_changed(self, combo):
        if self._suspend_prop:
            return
        c = self._cur_clip()
        if c is None:
            return
        self._push_undo("kb:%s" % self._sel_cell)
        c["kenburns"] = combo.get_active_id() or "none"
        self._save_project()
        self._render_story()

    def _on_vol_changed(self, scale):
        if self._suspend_prop:
            return
        c = self._cur_clip()
        if c is None:
            return
        self._push_undo("vol:%s" % self._sel_cell)
        c["volume"] = max(0.0, min(2.0, scale.get_value() / 100.0))
        self._save_project()
        self._render_story()

    def _on_mute_toggled(self, chk):
        if self._suspend_prop:
            return
        self._push_undo()
        c = self._cur_clip()
        if c is None:
            return
        c["mute"] = bool(chk.get_active())
        self._save_project()
        self._render_story()
        self._render_timeline()

    def _on_afade_toggled(self, chk):
        if self._suspend_prop:
            return
        self._push_undo()
        c = self._cur_clip()
        if c is not None:
            c["afade"] = bool(chk.get_active())
            self._save_project()

    def _on_vfade_toggled(self, chk):
        if self._suspend_prop:
            return
        self._push_undo()
        c = self._cur_clip()
        if c is not None:
            c["vfade"] = bool(chk.get_active())
            self._save_project()

    # ---- music field handlers ----
    def _on_mus_vol(self, scale):
        if self._suspend_prop or not self.music:
            return
        self._push_undo("musvol")
        self.music["volume"] = max(0.0, min(1.5, scale.get_value() / 100.0))
        self._save_project()

    def _on_mus_fade(self, _chk):
        if self._suspend_prop or not self.music:
            return
        self._push_undo()
        self.music["fadein"] = bool(self._mus_fadein.get_active())
        self.music["fadeout"] = bool(self._mus_fadeout.get_active())
        self._save_project()

    def _remove_music(self):
        self._push_undo(name="Remove music")
        self.music = None
        self._sel_music = False
        self._save_project()
        self._render_timeline()
        self._load_props(None)

    # ================= timeline =================
    def _timeline(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # panel-derived height (see _measure_panel): the full-height strip on a
        # roomy desktop, a shorter one on a 768-tall laptop panel so the preview
        # and the strip both fit without pushing the window off-screen.
        box.set_size_request(-1, self._tl_h)
        box.get_style_context().add_class("timeline")
        # Fixed-height strip pinned to the bottom. GTK3 propagates vexpand up from
        # descendants (the timeline's scale/rows), which made this box compute as
        # vertically expandable; as content's child it then swallowed the column's
        # slack, floating the 16:9 preview up and leaving blank gaps above and
        # below the timeline. Pin vexpand=False so the slack goes to the preview
        # area and the timeline sits flush at the bottom. (Same GTK quirk fixed in
        # the Music transport bar.)
        box.set_vexpand(False)

        # toolbar
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        bar.get_style_context().add_class("tlbar")

        seg = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        seg.get_style_context().add_class("seg")
        self.b_story = Gtk.Button(label=_t("Storyboard"))
        self.b_story.set_relief(Gtk.ReliefStyle.NONE)
        self.b_story.get_style_context().add_class("segbtn")
        self.b_story.get_style_context().add_class("active")
        self.b_story.connect("clicked", lambda *_: self._set_view("story"))
        self.b_time = Gtk.Button(label=_t("Timeline"))
        self.b_time.set_relief(Gtk.ReliefStyle.NONE)
        self.b_time.get_style_context().add_class("segbtn")
        self.b_time.connect("clicked", lambda *_: self._set_view("time"))
        seg.pack_start(self.b_story, False, False, 0)
        seg.pack_start(self.b_time, False, False, 0)
        bar.pack_start(seg, False, False, 0)

        zooms = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        # Real buttons, not icons in an EventBox. These look identical — the
        # squarebtn rule strips a button's padding and shadow so the 28x28
        # request is still the whole control — but a Button is in the focus
        # chain, so the timeline zoom is reachable by Tab and fires on Space
        # or Enter. An EventBox only ever hears a pointer.
        for ic in ("zoomout", "zoomin"):
            zb = Gtk.Button()
            zb.set_relief(Gtk.ReliefStyle.NONE)
            zb.get_style_context().add_class("squarebtn")
            zb.set_size_request(28, 28)
            zi = nbicons.image(ic, 14, FAINT)
            zi.set_halign(Gtk.Align.CENTER)
            zi.set_valign(Gtk.Align.CENTER)
            zi.set_hexpand(True)
            zi.set_vexpand(True)
            zb.add(zi)
            delta = 0.2 if ic == "zoomin" else -0.2
            zb.set_tooltip_text(_t("Zoom in") if ic == "zoomin" else "Zoom out")
            zb.connect("clicked", lambda _w, d=delta: self._zoom_by(d))
            zooms.pack_start(zb, False, False, 0)
        bar.pack_end(zooms, False, False, 0)
        box.pack_start(bar, False, False, 0)

        # stack of the two views
        self.tl_stack = Gtk.Stack()
        self.tl_stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.tl_stack.set_vexpand(True)
        self.tl_stack.add_named(self._storyboard_view(), "story")
        self.tl_stack.add_named(self._timeline_view(), "time")
        # The pager owns the switch from here on: it sets the type and duration
        # on EVERY call, so the order below is what gives the change a direction.
        # It is the order of the segmented control — Storyboard then Timeline —
        # so going to the Timeline slides forward and coming back slides back,
        # instead of both directions looking identical. Under Reduced Motion, and
        # on the no-compositor swrast fallback, nbmotion's policy resolves to
        # instant — exactly the NONE set above — so only accelerated sessions
        # animate. See de/nbtransitions.py.
        self._timeline_pager = nbtransitions.PageSwitcher(
            self.tl_stack, order=["story", "time"],
            duration=nbtransitions.PAGE)
        # The storyboard is where the editor opens: it is shown, not navigated
        # to, so it is stated as NONE rather than left to the default crossfade.
        # Routed through the pager anyway so the opening page is recorded —
        # otherwise the first real trip to the Timeline has no origin to measure
        # a direction against and would fade instead of sliding forward.
        self._timeline_pager.switch("story", direction=nbtransitions.NONE)
        box.pack_start(self.tl_stack, True, True, 0)
        return box

    def _storyboard_view(self):
        # The storyboard is REBUILT from the model on every change (dynamic clip
        # count), so this only creates the scrolling row container; _render_story
        # fills it with one card per clip, a transition connector between cards,
        # and a trailing "add clip" card.
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroll.get_style_context().add_class("storyscroll")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.get_style_context().add_class("storyrow")
        row.set_valign(Gtk.Align.CENTER)
        row.set_margin_start(24)
        row.set_margin_end(24)
        self._story_row = row
        scroll.add(row)
        return scroll

    def _story_card(self, k):
        """One storyboard card for clip index k (already validated)."""
        clip = self.clips[k]
        cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        cell.get_style_context().add_class("storycell")
        cell.get_style_context().add_class("storyfilled")
        cell.set_size_request(self._card_w(), self.STORY_H)
        cell.set_valign(Gtk.Align.CENTER)
        if k == self._sel_cell and not self._sel_music:
            cell.get_style_context().add_class("storysel")
        grp = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        grp.set_valign(Gtk.Align.CENTER)
        grp.set_vexpand(True)
        grp.pack_start(self._card_mat(clip), False, False, 0)
        num = Gtk.Label(label="%02d" % (k + 1))
        num.get_style_context().add_class("storynum")
        grp.pack_start(num, False, False, 0)
        if clip.get("kind") == "title":
            # the mat above already shows the card's own words
            nm = Gtk.Label(label=_t("Title card"))
            nm.get_style_context().add_class("storyname")
        else:
            media = self._clip_media(clip)
            nm = Gtk.Label(label=media["name"] if media else _t("Clip"))
            nm.get_style_context().add_class("storyname")
        nm.set_ellipsize(Pango.EllipsizeMode.END)
        nm.set_max_width_chars(15)
        grp.pack_start(nm, False, False, 0)
        dl = Gtk.Label(label=self._fmt_hms(self._clip_dur(clip)))
        dl.get_style_context().add_class("storymeta")
        grp.pack_start(dl, False, False, 0)
        badges = self._clip_badges(clip)
        if badges:
            bl = Gtk.Label(label=badges)
            bl.get_style_context().add_class("storybadge")
            bl.set_ellipsize(Pango.EllipsizeMode.END)
            bl.set_max_width_chars(16)
            grp.pack_start(bl, False, False, 0)
        cell.pack_start(grp, True, True, 0)
        evt = Gtk.Button()
        evt.set_relief(Gtk.ReliefStyle.NONE)
        evt.get_style_context().add_class("videohit")
        evt.get_style_context().add_class("storyhit")
        evt.set_tooltip_text(_t("Select clip %d: %s") % (k + 1, nm.get_text()))
        evt.add(cell)
        evt.connect("clicked", lambda _w, i=k: self._select_cell(i))
        return evt

    def _card_mat(self, clip):
        """The picture area at the top of a storyboard card: the clip's own
        frame for video and stills, the card's words for a title, a note glyph
        for a bare audio clip. Every card gets one, so the storyboard reads as a
        row of screens rather than a row of file names."""
        kind = clip.get("kind")
        media = self._clip_media(clip)
        if kind != "title" and media and media.get("kind") in ("video", "image"):
            img = Gtk.Image()
            img.set_halign(Gtk.Align.CENTER)
            img.get_style_context().add_class("storythumb")
            img.set_size_request(self.CARD_W, self.CARD_H)
            thumb = self._card_pixbuf(clip)
            if thumb is not None:
                # _scale_to_card returns a DEVICE-resolution pixbuf; as a plain
                # pixbuf it would draw the card at double size on a 2x panel.
                nbicons.set_image_pixbuf(img, thumb)
            self._card_imgs.setdefault(self._frame_key(clip), []).append(img)
            return img
        if kind == "title":
            pb = self._card_preview(clip, self.CARD_W, self.CARD_H)
            if pb is not None:
                img = Gtk.Image()
                nbicons.set_image_pixbuf(img, pb)
                img.set_halign(Gtk.Align.CENTER)
                img.get_style_context().add_class("storythumb")
                img.set_size_request(self.CARD_W, self.CARD_H)
                return img
        mat = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        mat.get_style_context().add_class("storythumb")
        mat.set_size_request(self.CARD_W, self.CARD_H)
        mat.set_halign(Gtk.Align.CENTER)
        if kind == "title":
            lbl = Gtk.Label(label=clip.get("cardtext") or _t("Title"))
            lbl.get_style_context().add_class("storymattext")
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            lbl.set_max_width_chars(14)
            lbl.set_valign(Gtk.Align.CENTER)
            mat.pack_start(lbl, True, True, 0)
        else:
            glyph = nbicons.image("music", 22, "#8A857A")
            glyph.set_halign(Gtk.Align.CENTER)
            glyph.set_valign(Gtk.Align.CENTER)
            mat.pack_start(glyph, True, True, 0)
        return mat

    def _clip_badges(self, clip):
        """A compact tag line summarising a clip's applied attributes, so the
        storyboard/timeline show at a glance what Movie-Maker edits are on it.
        Plain-text tags, never pictographic glyphs: the shipped Nimbus Sans has no
        ✦/⤢/♪/🔇 and they would render as tofu boxes on real hardware.

        Each tag is translated BEFORE the line is joined: the joined line goes
        into a Gtk.Label, and nbi18n's auto-translate looks the whole label up as
        one string, which never matches — so the badges were English everywhere.
        The caption tag said "Title", the same word this app uses for a title
        CARD, which is a different thing entirely."""
        b = []
        if clip.get("title"):
            b.append(_t("Caption"))
        if clip.get("effect", "none") != "none":
            b.append(_t("Effects"))
        if clip.get("kenburns", "none") != "none":
            b.append(_t("Zoom"))
        if float(clip.get("speed", 1.0)) != 1.0:
            b.append("%g×" % float(clip["speed"]))
        if clip.get("mute"):
            b.append(_t("Muted"))
        elif float(clip.get("volume", 1.0)) != 1.0:
            b.append(_t("Volume"))
        return "  ".join(b)

    def _story_connector(self, k):
        """The transition connector shown before clip k (its lead-in)."""
        clip = self.clips[k]
        tr = clip.get("transition")
        dot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        dot.get_style_context().add_class("transdot")
        dot.set_valign(Gtk.Align.CENTER)
        dot.set_halign(Gtk.Align.CENTER)
        dot.set_size_request(34, 34)
        if tr:
            dot.get_style_context().add_class("transdotset")
            img = nbicons.image(tr, 15, RED)
            img.set_halign(Gtk.Align.CENTER)
            img.set_valign(Gtk.Align.CENTER)
            dot.set_tooltip_text(_t(TRANS_NAME.get(tr) or "Transition"))
            dot.pack_start(img, True, True, 0)
        else:
            lbl = Gtk.Label(label="+")
            lbl.get_style_context().add_class("transplus")
            dot.pack_start(lbl, True, True, 0)
        return dot

    def _story_add_card(self):
        """The trailing dashed 'add clip' card that appends the selected bin
        item (or a title card via the Clip menu)."""
        cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        cell.get_style_context().add_class("storycell")
        cell.get_style_context().add_class("storyadd")
        cell.set_size_request(self._card_w(), self.STORY_H)
        cell.set_valign(Gtk.Align.CENTER)
        grp = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        grp.set_valign(Gtk.Align.CENTER)
        grp.set_vexpand(True)
        plus = nbicons.image("plus", 20, "#9A9484")
        plus.set_halign(Gtk.Align.CENTER)
        grp.pack_start(plus, False, False, 0)
        ready = (self.sel_media is not None
                 and 0 <= self.sel_media < len(self._bin))
        hint = Gtk.Label(label=_t("Add clip") if ready
                         else _t("Select media"))
        hint.get_style_context().add_class("storyhint")
        grp.pack_start(hint, False, False, 0)
        cell.pack_start(grp, True, True, 0)
        evt = Gtk.Button()
        evt.set_relief(Gtk.ReliefStyle.NONE)
        evt.get_style_context().add_class("videohit")
        evt.get_style_context().add_class("storyhit")
        evt.set_tooltip_text(_t("Add selected media") if ready else
                             _t("Select media before adding a clip"))
        evt.add(cell)
        evt.connect("clicked", lambda _w: self._append_selected_media())
        return evt

    # Timeline scale: pixels per second of footage, scaled by the zoom control.
    TL_BASE_PPS = 16.0
    TL_LANES = ("Video", "Audio", "Music", "Titles")

    def _pps(self):
        return max(4.0, self.TL_BASE_PPS * getattr(self, "_zoom", 1.0))

    def _tick_step(self, pps):
        """Seconds between ruler ticks, chosen so ticks land ~90px apart."""
        for s in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600):
            if s * pps >= 90.0:
                return s
        return 600

    def _timeline_view(self):
        # Everything scrolls horizontally together (ruler + all lanes share one
        # scroller), so a long movie pans as a unit and every lane stays aligned
        # to the same time origin. Clip widths are proportional to duration.
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroller.get_style_context().add_class("tlscroll")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        ruler = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        ruler.get_style_context().add_class("ruler")
        ruler.set_size_request(-1, 26)
        gutter = Gtk.Box()
        gutter.set_size_request(110, -1)
        gutter.get_style_context().add_class("rulergutter")
        ruler.pack_start(gutter, False, False, 0)
        overlay = Gtk.Overlay()
        ticks = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self._ruler_ticks = ticks           # rebuilt by _render_timeline
        overlay.add(ticks)
        head = Gtk.Box()
        head.get_style_context().add_class("playhead")
        head.set_size_request(2, -1)
        head.set_halign(Gtk.Align.START)
        head.set_valign(Gtk.Align.FILL)
        self._playhead = head
        overlay.add_overlay(head)
        overlay.set_overlay_pass_through(head, True)
        ruler.pack_start(overlay, False, False, 0)
        inner.pack_start(ruler, False, False, 0)

        for label in self.TL_LANES:
            track = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            track.get_style_context().add_class("track")
            track.set_vexpand(True)
            lg = Gtk.Label(label=_t(label.upper()), xalign=0)
            lg.get_style_context().add_class("tracklabel")
            lg.set_size_request(110, -1)
            track.pack_start(lg, False, False, 0)
            lane = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            lane.get_style_context().add_class("tracklane")
            self._lanes[label] = lane
            track.pack_start(lane, False, False, 0)
            inner.pack_start(track, True, True, 0)
        scroller.add(inner)
        return scroller

    # ================= model helpers =================
    def _clip_media(self, clip):
        mi = clip.get("media") if isinstance(clip, dict) else None
        if isinstance(mi, int) and 0 <= mi < len(self._bin):
            return self._bin[mi]
        return None

    def _sel_clip(self):
        k = self._sel_cell
        if isinstance(k, int) and 0 <= k < len(self.clips):
            return self.clips[k]
        return None

    def _clip_dur(self, clip):
        """A clip's on-timeline length in whole seconds (>=1)."""
        try:
            return max(1, int(round(float(clip.get("duration", 4) or 4))))
        except Exception:
            return 4

    def _max_clip_dur(self, clip):
        """Longest on-timeline length `clip` can actually fill: the source left
        after its trim-in point, divided by playback speed (export reads
        duration*speed source seconds from `start`). None when unbounded — a
        still image or media whose length couldn't be probed."""
        srcdur = self._clip_srcdur(clip)
        if not srcdur:
            return None
        speed = float(clip.get("speed", 1.0)) or 1.0
        return max(1, int(round((srcdur - float(clip.get("start", 0.0) or 0.0))
                                / speed)))

    def _layout(self):
        """Where every clip actually sits in the finished movie, and how long the
        movie is: [(offset, length, overlap)] and the total, in seconds.

        A transition OVERLAPS two clips, so a reel of two five-second clips with
        a one-second crossfade is nine seconds long, not ten. The whole app used
        to add the slots up instead: the ruler, the playhead, the Duration row
        and the Export dialog all reported a length the exported file did not
        have. This folds transitions by exactly the rule _build_ffmpeg_cmd
        renders them by, so every number in the app is the file's own."""
        out = []
        total = 0.0
        for i, c in enumerate(self.clips):
            d = float(self._clip_dur(c))
            td = 0.0
            if i > 0 and XFADE_NAME.get(c.get("transition")):
                td = min(TRANS_SECS, total, d)
                if td < TRANS_FLOOR:
                    td = 0.0
            out.append((max(0.0, total - td), d, td))
            total += d - td
        return out, total

    def _total(self):
        return self._layout()[1]

    def _src_duration(self, path):
        """Probed source length of a media file in seconds (float), memoised.
        0.0 when unknown (no ffprobe / not a timed medium). Bounds the trim
        controls so a clip can never seek past the end of its own source."""
        if not path:
            return 0.0
        cache = self._srcdur_cache
        if path in cache:
            return cache[path]
        dur = 0.0
        fp = shutil.which("ffprobe")
        if fp and os.path.isfile(path):
            try:
                r = subprocess.run(
                    [fp, "-v", "error", "-show_entries", "format=duration",
                     "-of", "csv=p=0", path],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, timeout=10)
                dur = float((r.stdout or b"0").strip() or 0)
            except Exception:
                dur = 0.0
        cache[path] = dur
        return dur

    def _bin_default_dur(self, media):
        """How long a clip placed from this bin item should run.

        The whole thing, for a video or a sound: dropping a ten-minute video on
        the storyboard and getting five seconds of it was a bad default, not a
        limit — the Length spinner already accepts the real length. Stills have
        no length of their own, so they keep the house default."""
        fallback = int(media.get("dur") or KIND_DUR.get(media.get("kind"), 4))
        if media.get("kind") == "image":
            return max(1, fallback)
        src = media.get("srcdur") or 0.0
        if not src:
            src = self._src_duration(media.get("path"))
            media["srcdur"] = src
        if not src:
            return max(1, fallback)         # unprobeable — fall back
        return max(1, min(3600, int(round(src))))

    def _clip_srcdur(self, clip):
        """Source seconds available for a clip (0 => unbounded, e.g. a still)."""
        media = self._clip_media(clip)
        if not media or media.get("kind") == "image":
            return 0.0
        d = media.get("srcdur") or 0.0
        if not d:
            d = self._src_duration(media.get("path"))
            media["srcdur"] = d
        return d

    def _fmt_hms(self, secs):
        secs = int(max(0, secs))
        return "%02d:%02d:%02d" % (secs // 3600, (secs % 3600) // 60, secs % 60)

    # ================= rendering =================
    def _render_all(self):
        self._render_bin()
        self._render_story()
        self._render_timeline()
        self._update_props_table()
        self._update_preview()

    def _render_story(self):
        """Rebuild the storyboard row from the model: one card per clip, a
        transition connector between adjacent cards, and a trailing add card."""
        row = getattr(self, "_story_row", None)
        if row is None:
            return
        for c in row.get_children():
            row.remove(c)
        self._story_cells = []
        self._card_imgs = {}
        for k in range(len(self.clips)):
            if k > 0:
                row.pack_start(self._story_connector(k), False, False, 0)
            card = self._story_card(k)
            self._story_cells.append(card)
            row.pack_start(card, False, False, 0)
        row.pack_start(self._story_add_card(), False, False, 0)
        row.show_all()

    def _render_transitions(self):
        # Transitions are drawn as part of the storyboard connectors and the
        # timeline transition lane, so a transition change just re-renders both.
        self._render_story()
        self._render_timeline()

    def _render_timeline(self):
        """Rebuild the ruler + every lane, proportional to real clip durations."""
        if not getattr(self, "_lanes", None):
            return
        pps = self._pps()
        layout, total = self._layout()
        # ruler ticks (time-accurate MM:SS)
        ticks = getattr(self, "_ruler_ticks", None)
        if ticks is not None:
            for c in ticks.get_children():
                ticks.remove(c)
            step = self._tick_step(pps)
            span = max(total, step * 4)
            t = 0
            while t <= span:
                lab = Gtk.Label(label="%02d:%02d" % (t // 60, t % 60), xalign=0)
                lab.get_style_context().add_class("tick")
                lab.set_size_request(max(1, int(round(step * pps))), -1)
                ticks.pack_start(lab, False, False, 0)
                t += step
            ticks.show_all()
        # per-clip lanes, each cell width proportional to duration
        for name in ("Video", "Audio", "Titles"):
            lane = self._lanes.get(name)
            if lane is None:
                continue
            for c in lane.get_children():
                lane.remove(c)
            for k, clip in enumerate(self.clips):
                # the clip's own share of the reel: its length less the part a
                # lead-in transition overlaps onto the clip before it, so the
                # lanes add up to the ruler and to the exported file
                _off, d, td = layout[k]
                w = max(6, int(round((d - td) * pps)))
                lane.pack_start(self._lane_cell(name, clip, w, k), False, False, 0)
            lane.show_all()
        # music lane: a single bar spanning the whole movie
        mlane = self._lanes.get("Music")
        if mlane is not None:
            for c in mlane.get_children():
                mlane.remove(c)
            if self.music:
                tw = max(6, int(round(max(total, 1) * pps)))
                cell = self._lane_cell_box(tw, sel=self._sel_music)
                chip = self._lane_chip("tlchipmusic", "tlchipname",
                                       self.music.get("name", "Music"),
                                       icon="music")
                cell.pack_start(chip, True, True, 0)
                mlane.pack_start(self._lane_click_wrap(
                    cell, music=True, label=_t("Select background music: %s") %
                    self.music.get("name", "Music")),
                                 False, False, 0)
            mlane.show_all()
        # playhead
        if self._playhead is not None:
            try:
                self._playhead.set_margin_start(int(round(self._play_pos * pps)))
            except Exception:
                pass

    # kept as an alias so transition/duration edits can refresh just the strip
    def _render_timeline_lanes(self):
        self._render_timeline()

    def _lane_cell_box(self, w, sel=False):
        cell = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        cell.set_size_request(w, -1)
        sc = cell.get_style_context()
        sc.add_class("lanecell")
        if sel:
            sc.add_class("lanesel")
        return cell

    def _lane_click_wrap(self, cell, k=None, music=False, label=None):
        evt = Gtk.Button()
        evt.set_relief(Gtk.ReliefStyle.NONE)
        evt.get_style_context().add_class("videohit")
        evt.get_style_context().add_class("lanehit")
        if label:
            evt.set_tooltip_text(label)
        evt.add(cell)
        if music:
            evt.connect("clicked", lambda _w: self._select_music())
        elif k is not None:
            evt.connect("clicked", lambda _w, i=k: self._select_cell(i))
        return evt

    def _lane_cell(self, name, clip, w, k):
        """A fixed-width, click-to-select lane cell for clip k. Carries a chip
        when the lane applies to the clip, else an empty spacer that keeps all
        lanes aligned to the same time origin."""
        kind = clip.get("kind")
        media = self._clip_media(clip)
        chip = None
        if name == "Video":
            if kind == "title":
                chip = self._lane_chip("tlchiptitle", "tlchipname",
                                       clip.get("cardtext") or "Title")
            elif kind in ("video", "image"):
                chip = self._lane_chip(
                    "tlchip", "tlchipname", media["name"] if media else "Clip",
                    icon=clip.get("transition"))
        elif name == "Audio":
            if kind == "audio" and not clip.get("mute"):
                chip = self._lane_chip("tlchipaudio", "tlchipname",
                                       media["name"] if media else "Audio",
                                       icon="music")
            elif kind == "video" and not clip.get("mute"):
                chip = self._lane_chip("tlchipaudio", "tlchipname", "Audio")
        elif name == "Titles":
            if clip.get("title"):
                chip = self._lane_chip("tlchipcap", "tlchipname", clip["title"])
        cell = self._lane_cell_box(
            w, sel=(k == self._sel_cell and not self._sel_music))
        if chip is not None:
            cell.pack_start(chip, True, True, 0)
        desc = (clip.get("cardtext") or _t("Title card")) if kind == "title" \
            else (media.get("name") if media else _t("Clip"))
        return self._lane_click_wrap(
            cell, k=k, label=_t("%s lane, clip %d: %s") %
            (_t(name), k + 1, desc))

    def _lane_chip(self, cls, labelcls, text, icon=None):
        chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        chip.get_style_context().add_class(cls)
        chip.set_valign(Gtk.Align.CENTER)
        if icon:
            img = nbicons.image(icon, 13, RED)
            img.set_valign(Gtk.Align.CENTER)
            chip.pack_start(img, False, False, 0)
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.get_style_context().add_class(labelcls)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        chip.pack_start(lbl, True, True, 0)
        return chip

    # ================= behaviour =================
    def _set_view(self, which):
        self._timeline_pager.switch(which)
        sc = self.b_story.get_style_context()
        tc = self.b_time.get_style_context()
        if which == "story":
            sc.add_class("active"); tc.remove_class("active")
        else:
            tc.add_class("active"); sc.remove_class("active")

    # -- transport --
    def _on_play(self, *_):
        # Real playback: a GLib.timeout clock sweeps the reel in time, advancing
        # the selection/preview across the filled clips, moving the timeline
        # playhead and running the timecode. No threads, nothing blocking. The
        # transport glyph is a stop square while playing, so a second press
        # stops and rewinds to the start.
        if self._playing:
            self._stop_playback(reset=True)
            return True
        if self._total() <= 0:
            return True          # nothing on the storyboard to play
        self._playing = True
        self._play_pos = 0.0
        self._play_last = GLib.get_monotonic_time()
        self._set_play_glyph(True)
        self._playback_sync(force=True)
        self._play_id = GLib.timeout_add(100, self._play_tick)
        return True

    def _play_tick(self):
        if not self._playing:
            self._play_id = 0
            return False
        now = GLib.get_monotonic_time()
        dt = (now - self._play_last) / 1000000.0
        self._play_last = now
        self._play_pos += dt
        if self._play_pos >= self._total():
            self._stop_playback(reset=True)
            return False
        self._playback_sync()
        return True

    def _playback_sync(self, force=False):
        """Map the playback clock onto the filled clips: select the current clip
        (which drives the preview + storyboard highlight), move the playhead and
        run the timecode. Filled slots concatenate — matching the export — so
        any gaps between them are skipped."""
        layout, total = self._layout()
        idx = None
        for k, (off, d, _td) in enumerate(layout):
            if self._play_pos < off + d:
                idx = k
                break
        if idx is None and self.clips:
            idx = len(self.clips) - 1
        if idx is not None and (force or idx != self._sel_cell):
            self._select_cell(idx)
            # A NEW clip: drop whatever was streaming so _playback_step opens
            # this one. Without this the player kept running the previous clip
            # while the timeline had already moved on — picture and playhead
            # describing different moments, which is worse than a still.
            self._live_clip = None
            self._playback_step(idx, layout[idx][0])
        elif idx is not None:
            # Within a clip, keep stepping the picture forward. Play used to hold
            # ONE frame per clip for the clip's whole length, so a film of four
            # five-second shots looked like four stills and nothing appeared to
            # be happening. This is not frame-accurate playback — the decoder is
            # a subprocess — but the picture now moves through each shot.
            self._playback_step(idx, layout[idx][0])
        if self._playhead is not None:
            try:
                self._playhead.set_margin_start(
                    int(self._play_pos * self._pps()))
            except Exception:
                pass
        # set the timecode last: _select_cell resets it to 00:00:00 / total
        try:
            self._tc.set_text(self._fmt_hms(self._play_pos) + " / "
                              + self._fmt_hms(total))
        except Exception:
            pass

    # How often playback asks the decoder for a fresh frame. One a second: each
    # is a whole ffmpeg process, and only one runs at a time (a new request
    # cancels the one in flight), so asking faster would mean cancelling every
    # request before it finished and the picture would never change at all.
    PLAY_STEP = 1.0

    def _on_clip_eos(self):
        """A clip's own stream ran out. The storyboard clock owns the running
        order, so this only parks the picture: _playback_sync will open the next
        clip when the playhead reaches it. Nothing is advanced here, or a clip
        that ends a shade early would skip ahead of the timeline."""
        if self._player is not None and self._player.available:
            self._player.pause()

    def _show_video_surface(self, on):
        """Swap between gtksink's surface and the still image. Only one is ever
        visible; both keep their size request so the stage cannot resize."""
        w = self._player.widget if self._player is not None else None
        if w is None:
            return
        try:
            if on:
                self._prev_image.hide()
                self._prev_glyph.hide()
                self._prev_label.hide()
                w.show()
            else:
                w.hide()
        except Exception:                                      # noqa: BLE001
            pass

    def _play_clip_live(self, idx, clip, into):
        """Start real playback of clip `idx` from `into` seconds in.

        True when the player took it. Only a `video` clip with a readable file
        goes this way: a still, a title card and an audio clip have no moving
        picture to stream, and the existing frame path already draws them
        correctly."""
        if self._player is None or not self._player.available:
            return False
        if clip.get("kind") != "video":
            return False
        path = clip.get("path")
        if not (path and os.path.isfile(path)):
            return False
        speed = float(clip.get("speed", 1.0) or 1.0)
        at = self._clip_start(clip) + into * speed
        if not self._player.open(path, at=at, rate=speed):
            return False
        self._player.play()
        self._show_video_surface(True)
        self._live_clip = idx
        return True

    def _relaunch_live_clip(self, clip):
        """A trim or speed edit landed on the clip that is STREAMING: drop
        the stream so the next transport tick (<=100ms away) reopens it under
        the new source mapping. _playback_step early-returns while _live_clip
        is pinned, so without this the stage keeps playing the OLD film — at
        the old rate, or showing footage the trim just cut — while the model,
        the timeline and the export all carry the edit. A duration edit does
        not come here: it moves the clock's exit point, not the mapping."""
        if not getattr(self, "_playing", False):
            return
        idx = getattr(self, "_live_clip", None)
        if idx is not None and 0 <= idx < len(self.clips) \
                and self.clips[idx] is clip:
            self._live_clip = None

    def _playback_step(self, idx, clip_off):
        """Keep the picture moving inside clip `idx`.

        A real moving clip is STREAMED — gtksink renders every frame with sound,
        which is what the Play button always claimed to do. The frame-by-frame
        path below is the fallback for a machine whose GStreamer cannot open the
        file, and it is what this used to be for everything: one ffmpeg process
        per second for a single frame, so a five-second shot was five stills and
        silence."""
        clip = self.clips[idx] if 0 <= idx < len(self.clips) else None
        if clip is None or clip.get("kind") != "video":
            self._show_video_surface(False)
            return
        into = max(0.0, self._play_pos - clip_off)
        if getattr(self, "_live_clip", None) == idx:
            return                       # already streaming this one
        if self._play_clip_live(idx, clip, into):
            return
        self._show_video_surface(False)
        if not (PIXBUF_OK and self._ffmpeg_ok()):
            return
        into = max(0.0, self._play_pos - clip_off)
        bucket = int(into / self.PLAY_STEP)
        if getattr(self, "_play_frame_at", None) == (idx, bucket):
            return
        self._play_frame_at = (idx, bucket)
        # a playback frame is never cached: one entry per second of a long film
        # would fill memory with pictures nobody will look at again
        self._pv_start(("play", idx, bucket), clip, cache=False,
                       seek=self._clip_start(clip) + into
                       * float(clip.get("speed", 1.0) or 1.0))

    def _stop_playback(self, reset=True):
        """Halt playback (idempotent). `reset` rewinds the clock, playhead and
        timecode to the start."""
        self._playing = False
        # so the next Play re-decodes the first frame instead of trusting the
        # bucket the last run stopped on
        self._play_frame_at = None
        # Release the stream and put the still image back. Stop must silence the
        # machine: a paused playbin holds the audio device, so a second app
        # opening sound after Stop would find it busy.
        self._live_clip = None
        if getattr(self, "_player", None) is not None:
            try:
                self._player.stop()
            except Exception:                                  # noqa: BLE001
                pass
        self._show_video_surface(False)
        if getattr(self, "_play_id", 0):
            try:
                GLib.source_remove(self._play_id)
            except Exception:
                pass
            self._play_id = 0
        self._set_play_glyph(False)
        if reset:
            self._play_pos = 0.0
            if self._playhead is not None:
                try:
                    self._playhead.set_margin_start(0)
                except Exception:
                    pass
            try:
                self._tc.set_text("00:00:00 / " + self._fmt_hms(self._total()))
            except Exception:
                pass

    def _set_play_glyph(self, playing):
        try:
            if self._play_img is not None:
                nbicons.set_image(self._play_img, "stopsq" if playing else "play", 18, FAINT)
            if getattr(self, "_play_w", None) is not None:
                self._play_w.set_tooltip_text(_t("Stop") if playing else "Play")
        except Exception:
            pass

    def _on_prev(self, *_):
        self._step_cell(-1)
        return True

    def _on_next(self, *_):
        self._step_cell(1)
        return True

    def _step_cell(self, delta):
        # Move the storyboard selection one clip; a manual skip ends playback.
        n = len(self.clips)
        if not n:
            return
        self._stop_playback(reset=True)
        cur = self._sel_cell
        if cur is None:
            nxt = 0 if delta > 0 else n - 1
        else:
            nxt = cur + delta
        nxt = max(0, min(n - 1, nxt))
        self._select_cell(nxt)

    # -- storyboard selection / placement --
    def _append_selected_media(self):
        """Append the selected bin item as a new clip at the end of the
        sequence (the storyboard 'add clip' card). Selects the new clip."""
        self._stop_playback(reset=True)
        if not (self.sel_media is not None
                and 0 <= self.sel_media < len(self._bin)):
            return
        m = self._bin[self.sel_media]
        self._push_undo()
        self.clips.append(_new_clip(self.sel_media, m["kind"],
                                    self._bin_default_dur(m)))
        self._save_project()
        self._render_all()
        self._select_cell(len(self.clips) - 1)

    def _insert_clip(self, clip, at, name=None):
        """Insert `clip` at position `at`, select it, repaint everything.
        `name` is what the Edit menu calls the step ("Undo Add Title Card")."""
        self._stop_playback(reset=True)
        self._push_undo(name=name)
        at = max(0, min(len(self.clips), at))
        self.clips.insert(at, clip)
        self._save_project()
        self._render_all()
        self._select_cell(at)

    def _move_clip(self, delta):
        """Reorder the selected clip left/right by one position."""
        k = self._sel_cell
        if k is None or not (0 <= k < len(self.clips)):
            return
        j = k + delta
        if not (0 <= j < len(self.clips)):
            return
        self._stop_playback(reset=True)
        self._push_undo(name="Move Left" if delta < 0 else "Move Right")
        self.clips[k], self.clips[j] = self.clips[j], self.clips[k]
        self._save_project()
        self._render_all()
        self._select_cell(j)

    def _select_cell(self, idx):
        self._sel_music = False
        if idx is None or idx < 0 or idx >= len(self.clips):
            self._sel_cell = None
        else:
            self._sel_cell = idx
        self._render_story()
        self._render_timeline()
        clip = self._sel_clip()
        # the palette reflects the selected clip's transition (none -> cleared)
        self._active_transition = clip.get("transition") if clip else None
        self._highlight_palette(self._active_transition)
        self._load_props(clip)
        self._update_preview()

    def _select_music(self):
        """Select the background-music strip (its own Properties editor)."""
        if not self.music:
            return
        self._stop_playback(reset=True)
        self._sel_music = True
        self._sel_cell = None
        self._active_transition = None
        self._highlight_palette(None)
        self._render_story()
        self._render_timeline()
        self._load_props(None)
        self._update_preview()

    # -- transition palette --
    def _highlight_palette(self, key):
        for k, cell in self._trans_cells.items():
            if cell is None:
                continue
            sc = cell.get_style_context()
            if k == key:
                cell.set_opacity(1.0); sc.add_class("transel")
            else:
                cell.set_opacity(0.55); sc.remove_class("transel")

    def _on_transition_click(self, _w, key):
        # A transition applies to the SELECTED storyboard clip (it is that clip's
        # lead-in). With no clip selected there is nothing to apply it to, so we
        # do nothing rather than light up a cell that maps to no clip and then
        # clears the instant a clip is picked — which reads as if selecting the
        # clip had removed the transition. The palette always mirrors the
        # selected clip.
        try:
            k = self._sel_cell
            if k is None or not (0 <= k < len(self.clips)):
                return True
            # toggling the same transition off is the natural second click
            cur = self.clips[k].get("transition")
            key = None if cur == key else key
            self._push_undo(name="Add Transition")
            self._active_transition = key
            self._highlight_palette(key)
            self.clips[k]["transition"] = key
            self._prop_trans.set_text(_t(TRANS_NAME.get(key) or "None"))
            self._render_story()
            self._render_timeline()
            self._save_project()
        except Exception:
            pass
        return True

    # -- timeline zoom --
    def _zoom_by(self, delta):
        try:
            self._zoom = max(0.6, min(2.4, getattr(self, "_zoom", 1.0) + delta))
            self._render_story()
            self._render_timeline()
        except Exception:
            pass
        return True

    # ================= import browser =================
    def _on_import(self, _btn):
        self._open_import()

    def _scan_media(self):
        """Recursively scan Home for real video/image/audio files. Hidden dirs
        and dotfiles are skipped; results are capped and name-sorted."""
        results = []
        seen = set()
        try:
            for root, dirs, files in os.walk(HOME):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for f in files:
                    if f.startswith("."):
                        continue
                    kind = _ext_kind(os.path.splitext(f)[1].lower())
                    if not kind:
                        continue
                    p = os.path.join(root, f)
                    if p in seen:
                        continue
                    seen.add(p)
                    results.append((p, f, kind))
                    if len(results) >= 400:
                        break
                if len(results) >= 400:
                    break
        except Exception:
            pass
        results.sort(key=lambda r: r[1].lower())
        return results

    def _open_import(self):
        # The Home scan (os.walk) runs on a worker thread so clicking Import is
        # instant — the dialog opens on a lightweight 'Scanning…' state and the
        # results are marshalled back with GLib.idle_add (installer.py pattern).
        self._stop_playback(reset=True)
        self._close_import()
        try:
            self._close_menu()
        except Exception:
            pass
        self._imp_results = []
        self._imp_selected = set()
        gen = getattr(self, "_imp_gen", 0) + 1
        self._imp_gen = gen

        # Size the scrim + centre the card off the LIVE window allocation,
        # falling back to the real primary-monitor size — never a hardcoded
        # 1920x1080 that would overflow / off-centre a smaller native panel.
        _sw, _sh = nbapp.screen_size()
        alloc = self.get_allocation()
        self._imp_wh = (alloc.width if alloc.width > 1 else _sw,
                        alloc.height if alloc.height > 1 else _sh)
        W, H = self._imp_wh
        layer = Gtk.Fixed()
        scrim = Gtk.EventBox()
        scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        scrim.set_size_request(W, H)
        scrim.connect("button-press-event",
                      lambda *a: (self._close_import(), True)[1])
        layer.put(scrim, 0, 0)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.get_style_context().add_class("impcard")
        card.set_size_request(560, -1)
        self._imp_card = card
        self._imp_populate_scanning(card)

        holder = Gtk.EventBox()   # own GdkWindow so it blits over the app body
        holder.add(card)
        self._imp_holder = holder
        layer.put(holder, 0, 0)
        self._overlay.add_overlay(layer)
        layer.show_all()
        self._imp_layer = layer
        self._recenter_import()

        threading.Thread(target=self._imp_scan_worker, args=(gen,),
                         daemon=True).start()

    def _imp_scan_worker(self, gen):
        try:
            results = self._scan_media()
        except Exception:
            results = []
        GLib.idle_add(self._imp_scan_done, gen, results)

    def _imp_scan_done(self, gen, results):
        # Back on the main thread. Drop the result if the dialog was closed or a
        # newer Import superseded this scan.
        if gen != getattr(self, "_imp_gen", 0):
            return False
        if getattr(self, "_imp_layer", None) is None:
            return False
        card = getattr(self, "_imp_card", None)
        if card is None:
            return False
        self._imp_results = results
        self._imp_populate(card, results)
        return False

    def _imp_populate_scanning(self, card):
        for c in card.get_children():
            card.remove(c)
        title = Gtk.Label(label=_t("Import Media"), xalign=0)
        title.get_style_context().add_class("dlgtitle")
        card.pack_start(title, False, False, 0)
        drop = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        drop.get_style_context().add_class("dlgdrop")
        d1 = Gtk.Label(label=_t("Scanning Home…"))
        d1.get_style_context().add_class("dropmain")
        drop.pack_start(d1, False, False, 0)
        card.pack_start(drop, False, False, 0)
        card.show_all()

    def _imp_populate(self, card, results):
        for c in card.get_children():
            card.remove(c)
        title = Gtk.Label(label=_t("Import Media"), xalign=0)
        title.get_style_context().add_class("dlgtitle")
        card.pack_start(title, False, False, 0)

        if not results:
            drop = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            drop.get_style_context().add_class("dlgdrop")
            d1 = Gtk.Label(label=_t("No video, image, or audio files in Home"))
            d1.get_style_context().add_class("dropmain")
            drop.pack_start(d1, False, False, 0)
            card.pack_start(drop, False, False, 0)
            ok = Gtk.Button(label=_t("OK"))
            ok.set_relief(Gtk.ReliefStyle.NONE)
            ok.get_style_context().add_class("dlgok")
            ok.set_halign(Gtk.Align.END)
            ok.set_margin_top(20)
            ok.connect("clicked", lambda *_: self._close_import())
            card.pack_start(ok, False, False, 0)
        else:
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.set_size_request(-1, 320)
            scroll.get_style_context().add_class("implist")
            self._imp_listbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            scroll.add(self._imp_listbox)
            card.pack_start(scroll, True, True, 0)
            self._imp_build_rows()

            footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            footer.set_margin_top(12)
            self._imp_count = Gtk.Label(label=_t("0 selected"), xalign=0)
            self._imp_count.get_style_context().add_class("impcount")
            footer.pack_start(self._imp_count, True, True, 0)
            cancel = Gtk.Button(label=_t("Cancel"))
            cancel.set_relief(Gtk.ReliefStyle.NONE)
            cancel.get_style_context().add_class("impbtn")
            cancel.connect("clicked", lambda *_: self._close_import())
            footer.pack_start(cancel, False, False, 0)
            self._imp_addbtn = Gtk.Button(label=_t("Add to Media"))
            self._imp_addbtn.set_relief(Gtk.ReliefStyle.NONE)
            self._imp_addbtn.get_style_context().add_class("dlgok")
            self._imp_addbtn.set_sensitive(False)
            self._imp_addbtn.connect("clicked", lambda *_: self._imp_confirm())
            footer.pack_start(self._imp_addbtn, False, False, 0)
            card.pack_start(footer, False, False, 0)

        card.show_all()
        # the card grew from the small 'Scanning…' state — re-centre it
        self._recenter_import()

    def _imp_build_rows(self):
        """Draw the scan results.

        The two kinds of row are not the same thing, so they are no longer built
        the same way. A file already in the bin is a STATEMENT — it says "Added"
        and there is nothing left to do to it — so it stays a plain box, out of
        the focus chain, rather than offering a keyboard user a stop that does
        nothing. A file that is not in the bin is a CONTROL, so it is a real
        button: reachable by Tab, activated by Space, and announced with the
        action it performs rather than only the file it names."""
        # A toggle rebuilds this whole list, destroying the very control the
        # user just activated. Note which row holds the keyboard focus so it can
        # be handed to that row's replacement; without this, every Space press
        # would throw a keyboard user back out of the list.
        focused = None
        for idx, btn in getattr(self, "_imp_rowbtns", {}).items():
            try:
                if btn.has_focus():
                    focused = idx
                    break
            except Exception:                                     # noqa: BLE001
                pass
        self._imp_rowbtns = {}
        for c in self._imp_listbox.get_children():
            self._imp_listbox.remove(c)
        for i, (p, name, kind) in enumerate(self._imp_results):
            already = any(m["path"] == p for m in self._bin)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=11)
            row.get_style_context().add_class("improw")
            if i in self._imp_selected:
                row.get_style_context().add_class("impsel")
            ico = nbicons.image(KIND_ICON.get(kind, "video"), 18, INK)
            ico.set_valign(Gtk.Align.CENTER)
            row.pack_start(ico, False, False, 0)
            txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            nm = Gtk.Label(label=name, xalign=0)
            nm.get_style_context().add_class("impname")
            nm.set_ellipsize(Pango.EllipsizeMode.END)
            nm.set_max_width_chars(34)
            txt.pack_start(nm, False, False, 0)
            try:
                rel = os.path.relpath(p, HOME)
            except Exception:
                rel = p
            pl = Gtk.Label(label=rel, xalign=0)
            pl.get_style_context().add_class("imppath")
            pl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            pl.set_max_width_chars(40)
            txt.pack_start(pl, False, False, 0)
            row.pack_start(txt, True, True, 0)
            if already:
                ad = Gtk.Label(label=_t("Added"), xalign=1)
                ad.get_style_context().add_class("impadded")
                ad.set_valign(Gtk.Align.CENTER)
                row.pack_end(ad, False, False, 0)
                holder = Gtk.Box()
                holder.set_can_focus(False)
                holder.add(row)
            else:
                holder = Gtk.Button()
                holder.set_relief(Gtk.ReliefStyle.NONE)
                holder.get_style_context().add_class("videohit")
                holder.get_style_context().add_class("imphit")
                # One whole string per action, not "Select" glued to a name:
                # a language that puts the verb elsewhere cannot fix a sentence
                # this app assembled out of fragments.
                action = (_t("Deselect %s (%s)") if i in self._imp_selected
                          else _t("Select %s (%s)"))
                action = action % (name, _t(KIND_LABEL.get(kind, "Clip")))
                holder.set_tooltip_text(action)
                # The row's own file-name label already names this button, so
                # nbapp's tooltip hook leaves the name alone and the action —
                # the part that changes as the row toggles — would never be
                # announced. Set it by hand.
                nbapp.name_control(holder, action)
                holder.connect("clicked", lambda _w, idx=i: self._imp_toggle(idx))
                holder.add(row)
                self._imp_rowbtns[i] = holder
            self._imp_listbox.pack_start(holder, False, False, 0)
        self._imp_listbox.show_all()
        back = self._imp_rowbtns.get(focused)
        if back is not None:
            back.grab_focus()

    def _imp_toggle(self, i):
        if i in self._imp_selected:
            self._imp_selected.discard(i)
        else:
            self._imp_selected.add(i)
        self._imp_build_rows()
        n = len(self._imp_selected)
        # formatted BEFORE it reaches set_text, so nbi18n's setter hook can no
        # longer recognise it — the count line has to be translated by hand
        self._imp_count.set_text(_t("%d selected") % n)
        self._imp_addbtn.set_sensitive(n > 0)
        self._imp_addbtn.set_label(_t("Add to Media") if n == 0
                                   else _t("Add %d to Media") % n)

    def _imp_confirm(self):
        # work out what is actually new first, so a confirm that brings in
        # nothing (an empty pick, or files already in the bin) does not leave a
        # do-nothing step on the undo history
        fresh = []
        for i in sorted(self._imp_selected):
            if i < 0 or i >= len(self._imp_results):
                continue
            p, name, kind = self._imp_results[i]
            if any(m["path"] == p for m in self._bin):
                continue
            fresh.append((p, name, kind))
        if fresh:
            self._push_undo(name="Import Media…")
        added = 0
        for p, name, kind in fresh:
            self._bin.append({"path": p, "name": name, "kind": kind,
                             "dur": KIND_DUR.get(kind, 4)})
            added += 1
        if added:
            self._save_project()
            self._render_bin()
            self._render_story()
        self._close_import()

    def _recenter_import(self):
        """Centre the import card on the live window. Re-run whenever the card's
        content changes height (scanning -> results/empty) so it stays centred
        instead of anchored to the small 'Scanning…' size."""
        layer = getattr(self, "_imp_layer", None)
        holder = getattr(self, "_imp_holder", None)
        if layer is None or holder is None:
            return
        try:
            W, H = getattr(self, "_imp_wh", nbapp.screen_size())
            _min, nat = holder.get_preferred_size()
            cw = nat.width if nat.width > 1 else 560
            ch = nat.height if nat.height > 1 else 320
            layer.move(holder, max((W - cw) // 2, 0), max((H - ch) // 2, 0))
        except Exception:
            pass

    def _close_import(self):
        layer = getattr(self, "_imp_layer", None)
        if layer is not None:
            try:
                self._overlay.remove(layer)
            except Exception:
                pass
            self._imp_layer = None
            return True
        return False

    # ================= export / render ==================
    # File ▸ Export Video assembles the filled storyboard slots into a single
    # 720p/24fps .mp4 in $NB_HOME/Videos via one ffmpeg filter_complex. Every
    # input is scaled + letterboxed to a common frame, so heterogeneous clips
    # (video, stills, audio->black card) stitch cleanly. Adjacent clips are
    # folded left-to-right: a boundary whose second clip carries a transition
    # is rendered with xfade (video) + acrossfade (audio); a plain boundary is a
    # hard-cut concat. Audio the UI accepted — audio-lane files and any video
    # clip's own track — is normalised and muxed as AAC (silence fills the gaps
    # so the audio timeline stays aligned with the video). The render runs as a
    # subprocess polled by GLib.timeout (no threads); progress is read from
    # ffmpeg's own -progress stream.
    def _open_export(self):
        self._stop_playback(reset=True)
        self._close_import()
        try:
            self._close_menu()
        except Exception:
            pass
        self._close_export()
        self._exp_done = False

        # Size the scrim + centre the card off the LIVE window allocation,
        # falling back to the real primary-monitor size — never a hardcoded
        # 1920x1080 that would overflow / off-centre a smaller native panel.
        _sw, _sh = nbapp.screen_size()
        alloc = self.get_allocation()
        W = alloc.width if alloc.width > 1 else _sw
        H = alloc.height if alloc.height > 1 else _sh
        layer = Gtk.Fixed()
        scrim = Gtk.EventBox()
        scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        scrim.set_size_request(W, H)
        scrim.connect("button-press-event", self._exp_scrim_press)
        layer.put(scrim, 0, 0)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.get_style_context().add_class("impcard")
        card.set_size_request(520, -1)
        title = Gtk.Label(label=_t("Export Video"), xalign=0)
        title.get_style_context().add_class("dlgtitle")
        card.pack_start(title, False, False, 0)

        filled = list(self.clips)
        if not filled:
            # _t: _exp_note hands the string straight to a label, so this panel
            # (and the damaged-install one below it) spoke English in every
            # language despite both being in all 17 catalogs.
            self._exp_note(card, _t("Storyboard is empty"))
        elif not self._ffmpeg_ok():
            self._exp_note(card, _t("Saving a video isn’t available"))
        else:
            self._exp_build_form(card)

        holder = Gtk.EventBox()   # own GdkWindow so it blits over the app body
        holder.add(card)
        layer.put(holder, 0, 0)
        self._overlay.add_overlay(layer)
        layer.show_all()
        try:
            _min, nat = holder.get_preferred_size()
            cw = nat.width if nat.width > 1 else 520
            ch = nat.height if nat.height > 1 else 400
            layer.move(holder, max((W - cw) // 2, 0), max((H - ch) // 2, 0))
        except Exception:
            pass
        self._exp_layer = layer

    def _exp_note(self, card, main):
        """A neutral drop-panel note + OK, for the empty / no-ffmpeg states."""
        drop = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        drop.get_style_context().add_class("dlgdrop")
        d1 = Gtk.Label(label=main)
        d1.set_line_wrap(True)
        d1.set_max_width_chars(46)
        # max-width-chars only caps the label's NATURAL width; at the default
        # halign FILL the panel still stretched it to its own width and the note
        # wrapped there instead of at the intended 46-character measure. Centre
        # it so the measure is what actually governs.
        d1.set_halign(Gtk.Align.CENTER)
        d1.get_style_context().add_class("dropmain")
        drop.pack_start(d1, False, False, 0)
        card.pack_start(drop, False, False, 0)
        ok = Gtk.Button(label=_t("OK"))
        ok.set_relief(Gtk.ReliefStyle.NONE)
        ok.get_style_context().add_class("dlgok")
        ok.set_halign(Gtk.Align.END)
        ok.set_margin_top(16)
        ok.connect("clicked", lambda *_: self._close_export())
        card.pack_start(ok, False, False, 0)

    def _exp_build_form(self, card):
        nl = Gtk.Label(label=_t("FILE NAME"), xalign=0)
        nl.get_style_context().add_class("propfieldlabel")
        card.pack_start(nl, False, False, 0)
        self._exp_name = Gtk.Entry()
        self._exp_name.get_style_context().add_class("propentry")
        default = (os.path.splitext(os.path.basename(self._path))[0]
                   if self._path else "Untitled Video")
        self._exp_name.set_text(default)
        self._exp_name.connect("changed", lambda *_: self._exp_update_path())
        card.pack_start(self._exp_name, False, False, 0)

        self._exp_path_lbl = Gtk.Label(label="", xalign=0)
        self._exp_path_lbl.get_style_context().add_class("imppath")
        self._exp_path_lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        card.pack_start(self._exp_path_lbl, False, False, 0)

        # Frame size. It was fixed at 720p with no way to say otherwise, and the
        # only place the number appeared was this dialog's summary line.
        sl = Gtk.Label(label=_t("SIZE"), xalign=0)
        sl.get_style_context().add_class("propfieldlabel")
        sl.set_margin_top(10)
        card.pack_start(sl, False, False, 0)
        seg = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        seg.get_style_context().add_class("seg")
        self._exp_size_btns = {}
        for w, h in EXPORT_SIZES:
            b = Gtk.Button(label="%d × %d" % (w, h))
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("segbtn")
            if (w, h) == (self._out_w, self._out_h):
                b.get_style_context().add_class("active")
            b.connect("clicked", lambda _b, ww=w, hh=h: self._exp_set_size(ww, hh))
            self._exp_size_btns[(w, h)] = b
            seg.pack_start(b, True, True, 0)
        card.pack_start(seg, False, False, 0)

        self._exp_info = Gtk.Label(label="", xalign=0)
        self._exp_info.get_style_context().add_class("impcount")
        self._exp_info.set_margin_top(10)
        card.pack_start(self._exp_info, False, False, 0)
        self._exp_update_info()

        self._exp_prog = Gtk.ProgressBar()
        self._exp_prog.get_style_context().add_class("expprog")
        self._exp_prog.set_no_show_all(True)
        self._exp_prog.set_margin_top(8)
        card.pack_start(self._exp_prog, False, False, 0)
        self._exp_status = Gtk.Label(label="", xalign=0)
        self._exp_status.get_style_context().add_class("impcount")
        self._exp_status.set_line_wrap(True)
        self._exp_status.set_max_width_chars(52)
        self._exp_status.set_no_show_all(True)
        card.pack_start(self._exp_status, False, False, 0)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        footer.set_margin_top(12)
        footer.pack_start(Gtk.Box(), True, True, 0)
        self._exp_cancel = Gtk.Button(label=_t("Cancel"))
        self._exp_cancel.set_relief(Gtk.ReliefStyle.NONE)
        self._exp_cancel.get_style_context().add_class("impbtn")
        self._exp_cancel.connect("clicked", lambda *_: self._close_export())
        footer.pack_start(self._exp_cancel, False, False, 0)
        # "Export", not "Render": the dialog is called Export Video and
        # "render" is video-trade language a first-time user does not have.
        self._exp_go = Gtk.Button(label=_t("Export"))
        self._exp_go.set_relief(Gtk.ReliefStyle.NONE)
        self._exp_go.get_style_context().add_class("dlgok")
        self._exp_go.connect("clicked", lambda *_: self._exp_go_click())
        footer.pack_start(self._exp_go, False, False, 0)
        card.pack_start(footer, False, False, 0)
        self._exp_update_path()

    def _exp_set_size(self, w, h):
        """Choose the export frame size. Remembered with the project."""
        self._out_w, self._out_h = w, h
        for key, b in getattr(self, "_exp_size_btns", {}).items():
            sc = b.get_style_context()
            (sc.add_class if key == (w, h) else sc.remove_class)("active")
        self._exp_update_info()
        self._save_project()

    def _exp_update_info(self):
        """The summary line: how many clips, how long the finished film runs, and
        the frame it will be written at. The length is the FOLDED length — what
        the file will actually be — not the sum of the slots, which is longer
        whenever a transition overlaps two clips."""
        try:
            n = len(self.clips)
            self._exp_info.set_text(
                "%s  ·  %s  ·  %d × %d  ·  %d fps"
                % (_t("%d clip%s") % (n, "" if n == 1 else "s"),
                   self._fmt_hms(self._total()),
                   self._out_w, self._out_h, EXPORT_FPS))
        except Exception:
            pass

    def _exp_sanitize(self, name):
        name = (name or "").strip().replace("/", "-").replace("\\", "-").strip()
        if name.lower().endswith(".mp4"):
            name = name[:-4].strip()
        return name or "Untitled Video"

    def _exp_update_path(self):
        try:
            name = self._exp_sanitize(self._exp_name.get_text())
            self._exp_path_lbl.set_text(os.path.join("Videos", name + ".mp4"))
        except Exception:
            pass

    def _exp_go_click(self):
        # After a successful render the same button reveals the file; otherwise
        # it starts the render.
        if self._exp_done:
            self._reveal_videos()
        else:
            self._exp_start()

    def _seg_dur(self, s):
        """A clip's duration in whole seconds (>=1), tolerant of bad data."""
        try:
            return max(1, int(s.get("duration", 4) or 4))
        except Exception:
            return 4

    def _probe_has_audio(self, path):
        """True if `path` carries a decodable audio stream, probed with ffprobe.

        Result is memoised per path. When ffprobe is absent we return False —
        a wrong 'yes' would map a missing [i:a] pad and fail the whole render,
        so we only pull a video clip's own track when we can confirm it exists;
        dedicated audio-lane files are always included regardless."""
        cache = getattr(self, "_audio_probe_cache", None)
        if cache is None:
            cache = self._audio_probe_cache = {}
        if path in cache:
            return cache[path]
        ok = False
        fp = shutil.which("ffprobe")
        if fp and path and os.path.isfile(path):
            try:
                r = subprocess.run(
                    [fp, "-v", "error", "-select_streams", "a",
                     "-show_entries", "stream=index", "-of", "csv=p=0", path],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, timeout=10)
                ok = bool(r.stdout and r.stdout.strip())
            except Exception:
                ok = False
        cache[path] = ok
        return ok

    # ---- export encoder selection ----------------------------------------
    def _video_encoder(self):
        """The best H.264-class video encoder this ffmpeg actually has, as
        codec args. Probed once. libx264 is preferred (quality/size); a build
        without it falls back to libopenh264, then to the universally-present
        MPEG-4 Part 2 encoder — so an export always produces a playable file
        rather than dying on a missing 'libx264'.

        The PROBE is cached, never the finished args: those depend on the chosen
        frame size, and caching them handed a 360p export a 1080p bit rate."""
        have = getattr(self, "_venc_have", None)
        if have is None:
            have = set()
            ff = self._ffmpeg_path()
            if ff:
                try:
                    r = subprocess.run(
                        [ff, "-hide_banner", "-encoders"],
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, timeout=8)
                    for line in (r.stdout or b"").decode(
                            "utf-8", "replace").splitlines():
                        p = line.split()
                        if len(p) >= 2 and p[0] and p[0][0] in "VAS":
                            have.add(p[1])
                except Exception:
                    pass
            self._venc_have = have
        # Bit rate for the fixed-rate fallbacks is scaled off the frame area so a
        # 360p export is not given a 1080p budget (or the reverse).
        mbit = max(1.2, 3.0 * (self._out_w * self._out_h) / (1280.0 * 720.0))
        if "libx264" in have:
            enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21"]
        elif "libopenh264" in have:
            enc = ["-c:v", "libopenh264", "-b:v", "%.1fM" % mbit]
        else:
            enc = ["-c:v", "mpeg4", "-b:v", "%.1fM" % (mbit * 1.6)]
        enc += ["-pix_fmt", "yuv420p"]
        return enc

    # ---- export filter builders ------------------------------------------
    def _atempo_chain(self, speed):
        """atempo fragments realising `speed` (each stage is 0.5..2.0). Our four
        speed presets all fit a single stage, but chain defensively anyway."""
        speed = max(0.25, min(4.0, float(speed)))
        stages = []
        while speed > 2.0:
            stages.append(2.0)
            speed /= 2.0
        while speed < 0.5:
            stages.append(0.5)
            speed /= 0.5
        stages.append(speed)
        return ",".join("atempo=%.4f" % s for s in stages)

    def _kenburns_filter(self, mode, dur):
        """A pan/zoom (Ken Burns) chain for a single still, producing exactly
        `dur` seconds at the export frame. Pre-scaled large so the motion stays
        smooth rather than stepping pixel-by-pixel."""
        W, H, FPS = self._out_w, self._out_h, EXPORT_FPS
        frames = max(1, int(round(dur * FPS)))
        pre = ("scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d"
               % (W * 2, H * 2, W * 2, H * 2))
        cx, cy = "'iw/2-(iw/zoom/2)'", "'ih/2-(ih/zoom/2)'"
        inc = 0.5 / frames
        if mode == "out":
            z = "'if(lte(on,1),1.5,max(zoom-%.6f,1.0))'" % inc
            x, y = cx, cy
        elif mode == "left":
            z = "1.2"
            x = "'(iw-iw/zoom)*(1-on/%d)'" % frames
            y = cy
        elif mode == "right":
            z = "1.2"
            x = "'(iw-iw/zoom)*(on/%d)'" % frames
            y = cy
        else:   # "in" (and any unknown)
            z = "'min(zoom+%.6f,1.5)'" % inc
            x, y = cx, cy
        return ("%s,zoompan=z=%s:x=%s:y=%s:d=%d:s=%dx%d:fps=%d"
                % (pre, z, x, y, frames, W, H, FPS))

    def _video_base_filter(self, kind, dur, speed, effect, kenburns):
        """The per-clip video chain (no input label, no trailing format) that
        normalises any source to the export frame and bakes in speed + effect,
        and that is EXACTLY `dur` seconds long whatever the source did."""
        W, H, FPS = self._out_w, self._out_h, EXPORT_FPS
        parts = []
        if kind == "image" and kenburns and kenburns != "none":
            parts.append(self._kenburns_filter(kenburns, dur))
        else:
            parts.append("scale=%d:%d:force_original_aspect_ratio=decrease"
                         % (W, H))
            parts.append("pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=%s"
                         % (W, H, EXPORT_BG))
            if kind == "video" and speed != 1.0:
                parts.append("setpts=%.6f*PTS" % (1.0 / speed))
            parts.append("fps=%d" % FPS)
        ef = EFFECT_VF.get(effect, "")
        if ef:
            parts.append(ef)
        # THE LENGTH LOCK. Every [v{n}] must be exactly its slot length, because
        # everything downstream — the transition offsets, the concat, the music
        # mix, the reported duration — is computed from that number.
        #
        # The audio side was already padded to the slot (apad + atrim); the
        # picture was not, and nothing else in the graph could tell. A clip whose
        # source ran out mid-slot (a video asked to run longer than the file, a
        # trim-in point near the end, 2x speed over a short clip, a project whose
        # media was replaced by a shorter file, any media ffprobe could not
        # measure so the Length field was never bounded) produced a video track
        # SHORTER than the movie: the picture stopped early, every later clip
        # played at the wrong time, and the tail of the film was black — while
        # the export reported success. tpad holds the last frame out to the slot
        # and trim cuts anything over it, so the picture can no longer disagree
        # with the timeline.
        # The fps between tpad and trim is not redundant: tpad's cloned frames
        # come out on a different step from the ones it padded, and trim then cut
        # two frames late on every pan-and-zoom still (a 10-second reel of them
        # ran 10.083). Re-imposing the frame rate first makes trim exact.
        parts.append("tpad=stop_mode=clone:stop_duration=%.3f" % dur)
        parts.append("fps=%d,trim=duration=%.3f,setpts=PTS-STARTPTS"
                     % (FPS, dur))
        return ",".join(parts)

    def _audio_clip_filter(self, a_in, clip, dur, speed, kind, n):
        """(has_real_audio, statement) building [a{n}] for one clip — its real
        track (trimmed, sped, volume/fade applied) or matched silence."""
        if a_in is None:
            return (False,
                    "anullsrc=r=%d:cl=stereo,atrim=0:%.3f,"
                    "asetpts=PTS-STARTPTS[a%d]" % (AUDIO_RATE, dur, n))
        f = []
        if kind == "video" and speed != 1.0:
            f.append(self._atempo_chain(speed))
        f.append("aresample=%d" % AUDIO_RATE)
        f.append("aformat=sample_rates=%d:channel_layouts=stereo" % AUDIO_RATE)
        vol = float(clip.get("volume", 1.0))
        if abs(vol - 1.0) > 1e-3:
            f.append("volume=%.3f" % max(0.0, min(2.0, vol)))
        if clip.get("afade"):
            f.append("afade=t=in:st=0:d=0.4")
            f.append("afade=t=out:st=%.3f:d=0.6" % max(0.0, dur - 0.6))
        f += ["apad", "atrim=0:%.3f" % dur, "asetpts=PTS-STARTPTS"]
        return (True, "[%d:a]%s[a%d]" % (a_in, ",".join(f), n))

    def _render_start(self, clip, dur, speed):
        """A clip's trim-in point, held inside its own source.

        A start point at or past the end of the file makes ffmpeg decode zero
        frames. That used to export an .mp4 with NO VIDEO STREAM AT ALL, and
        report it as saved. Reachable from a project file whose media was
        replaced by a shorter file, or from any source ffprobe could not measure
        (the Trim field is only bounded when the length is known)."""
        start = max(0.0, float(clip.get("start", 0.0) or 0.0))
        srcdur = self._clip_srcdur(clip)
        if srcdur and start > srcdur - 0.25:
            start = max(0.0, srcdur - max(0.25, min(dur * speed, srcdur)))
        return start

    def _missing_media(self, segs):
        """The names of clips whose media file is no longer on disk.

        Such a clip renders as an empty coloured slot. That is the right thing
        to render — the rest of the movie is still worth having — but the export
        must say it happened rather than hand back a film with silent gaps."""
        names = []
        for s in segs:
            if s.get("kind") == "title":
                continue
            media = self._clip_media(s)
            if media is None:
                names.append(_t("Clip"))
            elif not os.path.isfile(media.get("path", "")):
                names.append(media.get("name") or _t("Clip"))
        seen = []
        for n in names:
            if n not in seen:
                seen.append(n)
        return seen

    def _build_ffmpeg_cmd(self, segs, out, progress_file):
        """Assemble the ffmpeg render command. Returns (argv, total_secs, err).

        Each clip is normalised to a common 720p frame ([v{n}]) and 48k stereo
        ([a{n}]), with its Movie-Maker edits baked in: trim (-ss/-t), speed
        (setpts + atempo), visual effect, pan/zoom, caption overlay, per-clip
        volume/mute and audio/video fades. Standalone title cards render from
        cairo. Adjacent clips fold left-to-right (xfade+acrossfade on a lead-in
        transition, else hard-cut concat). A background-music track, if set, is
        mixed under the whole movie."""
        ff = self._ffmpeg_path()
        if not ff:
            # err is shown to the user verbatim, so it is a sentence, not the
            # name of an internal tool
            return None, 0, _t("Saving a video isn’t available")
        self._exp_tmp_imgs = []          # generated caption/card PNGs
        self._exp_gone = self._missing_media(segs)
        args = [ff, "-nostdin", "-y"]
        vstmts = []                      # per-clip video statements -> [v{n}]
        astmts = []                      # per-clip audio statements -> [a{n}]
        seg_info = []                    # (dur, transition) in fold order
        color = "color=c=%s:s=%dx%d:r=%d" % (
            EXPORT_BG, self._out_w, self._out_h, EXPORT_FPS)
        in_idx = 0
        n = 0
        any_audio = False
        for s in segs:
            kind = s.get("kind", "video")
            dur = self._clip_dur(s)
            speed = float(s.get("speed", 1.0) or 1.0)
            start = self._render_start(s, dur, speed)
            effect = s.get("effect", "none")
            kenburns = s.get("kenburns", "none")
            media = self._clip_media(s)
            path = media.get("path") if media else None
            have = bool(path and os.path.isfile(path))
            a_in = None
            cap_in = None
            # ---- declare this clip's input(s) ----
            if kind == "title":
                png = self._render_card_png(s.get("cardtext", ""),
                                            s.get("cardsub", ""))
                if png:
                    args += ["-loop", "1", "-framerate", str(EXPORT_FPS),
                             "-t", "%.3f" % dur, "-i", png]
                else:
                    args += ["-f", "lavfi", "-t", "%.3f" % dur, "-i", color]
                v_in = in_idx
                in_idx += 1
            elif kind == "image" and have:
                if kenburns and kenburns != "none":
                    # ONE frame in, which zoompan's d= expands to exactly this
                    # clip's length. It used to be "-loop 1" with no "-t" on the
                    # belief that zoompan set the length; it does not — zoompan
                    # emits d frames for EVERY frame it is fed, so an endlessly
                    # looping still made an endless stream and the export ran
                    # for ever at "100%", with Cancel the only way out. Any
                    # photo with Pan & Zoom on it hit this.
                    args += ["-i", path]
                else:
                    args += ["-loop", "1", "-framerate", str(EXPORT_FPS),
                             "-t", "%.3f" % dur, "-i", path]
                v_in = in_idx
                in_idx += 1
            elif kind == "video" and have:
                if start > 0:
                    args += ["-ss", "%.3f" % start]
                args += ["-t", "%.3f" % max(0.1, dur * speed), "-i", path]
                v_in = in_idx
                in_idx += 1
                if not s.get("mute") and self._probe_has_audio(path):
                    a_in = v_in
            elif kind == "audio" and have:
                args += ["-f", "lavfi", "-t", "%.3f" % dur, "-i", color]
                v_in = in_idx
                in_idx += 1
                if not s.get("mute"):
                    if start > 0:
                        args += ["-ss", "%.3f" % start]
                    args += ["-t", "%.3f" % dur, "-i", path]
                    a_in = in_idx
                    in_idx += 1
            else:
                args += ["-f", "lavfi", "-t", "%.3f" % dur, "-i", color]
                v_in = in_idx
                in_idx += 1
            # caption overlay input (media clips only; title cards ARE text)
            cap_text = s.get("title", "") if kind in ("video", "image") else ""
            if cap_text:
                cpng = self._render_caption_png(cap_text)
                if cpng:
                    args += ["-loop", "1", "-framerate", str(EXPORT_FPS),
                             "-t", "%.3f" % dur, "-i", cpng]
                    cap_in = in_idx
                    in_idx += 1
            # ---- video statement -> [v{n}] ----
            base = self._video_base_filter(kind, dur, speed, effect, kenburns)
            fade = ""
            if s.get("vfade"):
                fade = (",fade=t=in:st=0:d=0.5:color=black"
                        ",fade=t=out:st=%.3f:d=0.6:color=black"
                        % max(0.0, dur - 0.6))
            # xfade demands BOTH of these on every [v{n}], and they are
            # different things: settb fixes the TIMEBASE (overlay emits
            # microseconds, which xfade refuses to fold against a plain
            # clip's 1/fps), and fps fixes the FRAME RATE, which trim +
            # setpts leave unknown -- xfade then reads rate 1/0 and fails
            # the whole export with "inputs needs to be a constant frame
            # rate". It must come AFTER setpts: pinning it only after the
            # xfade instead breaks folding again.
            if cap_in is not None:
                vstmts.append("[%d:v]%s[b%d]" % (v_in, base, n))
                # overlay leaves the clip one frame long (its own timebase runs
                # in microseconds and the caption input is its own stream), so
                # the length lock is re-applied on this side of it too
                vstmts.append("[b%d][%d:v]overlay=0:0%s,setsar=1,settb=1/%d,"
                              "trim=end_frame=%d,setpts=PTS-STARTPTS,"
                              "fps=%d,format=yuv420p[v%d]"
                              % (n, cap_in, fade, EXPORT_FPS,
                                 max(1, int(round(dur * EXPORT_FPS))),
                                 EXPORT_FPS, n))
            else:
                vstmts.append(
                    "[%d:v]%s%s,fps=%d,setsar=1,settb=1/%d,"
                    "format=yuv420p[v%d]"
                    % (v_in, base, fade, EXPORT_FPS, EXPORT_FPS, n))
            # ---- audio statement -> [a{n}] ----
            real, astmt = self._audio_clip_filter(a_in, s, dur, speed, kind, n)
            any_audio = any_audio or real
            astmts.append(astmt)
            seg_info.append((dur, s.get("transition")))
            n += 1
        if n == 0:
            return None, 0, _t("Storyboard is empty")

        # Only emit the audio graph when it will actually be mapped, else the
        # per-clip [a{n}] statements dangle and ffmpeg refuses the graph.
        want_audio = any_audio or bool(self.music)
        parts = list(vstmts)
        if want_audio:
            parts += astmts
        cur_v = "v0"
        cur_a = "a0"
        cur_len = float(seg_info[0][0])
        for i in range(1, n):
            d_i, tr = seg_info[i]
            xf = XFADE_NAME.get(tr) if tr else None
            td = min(TRANS_SECS, cur_len, float(d_i)) if xf else 0.0
            if xf and td >= TRANS_FLOOR:
                off = max(0.0, cur_len - td)
                parts.append(
                    "[%s][v%d]xfade=transition=%s:duration=%.3f:offset=%.3f"
                    ",settb=1/%d[vx%d]" % (cur_v, i, xf, td, off, EXPORT_FPS, i))
                cur_v = "vx%d" % i
                if want_audio:
                    parts.append("[%s][a%d]acrossfade=d=%.3f[ax%d]"
                                 % (cur_a, i, td, i))
                    cur_a = "ax%d" % i
                cur_len = cur_len + d_i - td
            else:
                parts.append("[%s][v%d]concat=n=2:v=1:a=0,settb=1/%d[vc%d]"
                             % (cur_v, i, EXPORT_FPS, i))
                cur_v = "vc%d" % i
                if want_audio:
                    parts.append("[%s][a%d]concat=n=2:v=0:a=1[ac%d]"
                                 % (cur_a, i, i))
                    cur_a = "ac%d" % i
                cur_len = cur_len + d_i
        final_a = cur_a
        # ---- background music mixed under the whole movie ----
        if self.music and os.path.isfile(self.music.get("path", "")):
            args += ["-i", self.music["path"]]
            m_in = in_idx
            in_idx += 1
            mv = max(0.0, min(1.5, float(self.music.get("volume", 0.6))))
            mf = []
            if self.music.get("fadein", True):
                mf.append("afade=t=in:st=0:d=1.2")
            if self.music.get("fadeout", True):
                mf.append("afade=t=out:st=%.3f:d=1.5"
                          % max(0.0, cur_len - 1.5))
            fadefrag = ("," + ",".join(mf)) if mf else ""
            parts.append(
                "[%d:a]aresample=%d,aformat=sample_rates=%d:"
                "channel_layouts=stereo,volume=%.3f%s,apad,atrim=0:%.3f,"
                "asetpts=PTS-STARTPTS[mez]"
                % (m_in, AUDIO_RATE, AUDIO_RATE, mv, fadefrag, cur_len))
            parts.append(
                "[%s][mez]amix=inputs=2:duration=first:dropout_transition=0:"
                "normalize=0[amix]" % final_a)
            final_a = "amix"
        if want_audio:
            # One limiter across the finished soundtrack. A clip's volume goes to
            # 200% and background music is mixed in WITHOUT normalisation (which
            # is right — normalising would duck the film's own sound whenever the
            # music came in), so the sum could exceed full scale and the export
            # came out audibly distorted with nothing to say why. A lookahead
            # limiter holds the peaks and leaves everything under them alone.
            parts.append("[%s]alimiter=limit=0.97:level=disabled[aout]"
                         % final_a)
            final_a = "aout"
        fc = ";".join(parts)
        args += ["-filter_complex", fc, "-map", "[%s]" % cur_v]
        if want_audio:
            args += ["-map", "[%s]" % final_a]
        args += self._video_encoder()
        if want_audio:
            args += ["-c:a", "aac", "-b:a", "192k"]
        args += ["-movflags", "+faststart"]
        if progress_file:
            args += ["-progress", progress_file]
        args += [out]
        return args, max(1, int(round(cur_len))), None

    # ---- cairo-rendered title cards & captions ---------------------------
    def _save_tmp_png(self, surf):
        try:
            fd, p = tempfile.mkstemp(prefix="nbvid-img-", suffix=".png")
            os.close(fd)
            surf.write_to_png(p)
            self._exp_tmp_imgs.append(p)
            return p
        except Exception:
            return None

    # Text burned into a video frame goes through PANGO, never cairo's toy font
    # API (select_font_face / show_text). The toy API picks one face and does no
    # per-character fallback, so a title or caption typed in Japanese, Chinese,
    # Korean, Hindi or Yiddish — five of this system's seventeen interface
    # languages — drew NOTHING AT ALL: the export succeeded and handed back a
    # blank card. Pango resolves each run against the installed faces, and a
    # layout also gives us the measure the toy API never did, so a long title
    # wraps and shrinks to fit the frame instead of running off both edges.
    def _text_layout(self, cr, text, size, maxw, maxh, bold=False):
        """A centred, wrapped Pango layout for `text` at most `maxw` x `maxh`,
        shrinking the type until it fits. Returns (layout, width, height)."""
        from gi.repository import PangoCairo
        layout = PangoCairo.create_layout(cr)
        layout.set_width(int(maxw * Pango.SCALE))
        layout.set_alignment(Pango.Alignment.CENTER)
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        layout.set_text(text, -1)
        size = float(size)
        while True:
            fd = Pango.FontDescription("Nimbus Sans")
            if bold:
                fd.set_weight(Pango.Weight.BOLD)
            fd.set_absolute_size(size * Pango.SCALE)
            layout.set_font_description(fd)
            w, h = layout.get_pixel_size()
            if h <= maxh or size <= 12.0:
                return layout, w, h
            size = max(12.0, size * 0.82)

    def _draw_text_block(self, cr, text, size, cx, cy, maxw, maxh, bold=False):
        """Draw `text` centred on (cx, cy), wrapped and shrunk to fit."""
        from gi.repository import PangoCairo
        layout, w, h = self._text_layout(cr, text, size, maxw, maxh, bold=bold)
        cr.move_to(cx - maxw / 2.0, cy - h / 2.0)
        PangoCairo.show_layout(cr, layout)
        return h

    def _render_card_png(self, text, sub):
        """A full-frame title / credits card (dark stage, centred title +
        subtitle) as a temp PNG, or None if cairo is unavailable."""
        try:
            import cairo
        except Exception:
            return None
        try:
            W, H = self._out_w, self._out_h
            k = H / 720.0                       # scale the layout with the frame
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
            cr = cairo.Context(surf)
            cr.set_source_rgb(0x16 / 255.0, 0x15 / 255.0, 0x0F / 255.0)
            cr.paint()
            measure = W * 0.78
            cr.set_source_rgb(0.99, 0.98, 0.97)
            th = self._draw_text_block(cr, text or "", 76 * k, W / 2.0,
                                       H * 0.44, measure, H * 0.42, bold=True)
            y = H * 0.44 + th / 2.0
            if sub:
                cr.set_source_rgb(0.80, 0.78, 0.74)
                sh = self._draw_text_block(cr, sub, 34 * k, W / 2.0,
                                           y + 40 * k, measure, H * 0.18)
                y += 40 * k + sh / 2.0
            cr.set_source_rgb(0.78, 0.20, 0.12)      # accent rule beneath
            rw = 128 * k
            cr.rectangle(W / 2.0 - rw / 2.0, min(y + 34 * k, H - 24 * k),
                         rw, max(2.0, 3 * k))
            cr.fill()
            return self._save_tmp_png(surf)
        except Exception:
            return None

    def _render_caption_png(self, text):
        """A transparent full-frame overlay with a caption bar near the bottom,
        as a temp PNG, or None if cairo is unavailable."""
        try:
            import cairo
        except Exception:
            return None
        try:
            W, H = self._out_w, self._out_h
            k = H / 720.0
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
            cr = cairo.Context(surf)
            margin = 44 * k
            # measure the text first so the bar is drawn around it: a two-line
            # caption used to overflow a fixed-height bar
            _l, _w, th = self._text_layout(cr, text, 40 * k, W * 0.86,
                                           H * 0.34)
            bar_h = th + 34 * k
            cr.set_source_rgba(0, 0, 0, 0.42)
            cr.rectangle(0, H - bar_h - margin, W, bar_h)
            cr.fill()
            cr.set_source_rgb(1, 1, 1)
            self._draw_text_block(cr, text, 40 * k, W / 2.0,
                                  H - margin - bar_h / 2.0, W * 0.86, H * 0.34)
            return self._save_tmp_png(surf)
        except Exception:
            return None

    def _exp_start(self):
        segs = list(self.clips)
        if not segs:
            self._exp_show_status(_t("Storyboard is empty"), error=True)
            return
        name = self._exp_sanitize(self._exp_name.get_text())
        out = os.path.join(VIDEOS_DIR, name + ".mp4")
        # The name defaults to the project's, the folder is always Videos, and
        # an exported video comes back into the media bin on the next Home
        # scan — so exporting twice onto one name is an ordinary thing to do.
        # It used to destroy the first video without a word (ffmpeg runs with
        # -y). Ask first, in the same words the house Save dialog uses.
        if os.path.exists(out):
            self._confirm(
                _t("Replace video?"),
                _t("“%s.mp4” is already in Videos. Exporting replaces it.")
                % name,
                _t("Replace"), lambda: self._exp_render(segs, out))
            return
        self._exp_render(segs, out)

    def _exp_render(self, segs, out):
        """Begin the render. Split from _exp_start so the replace-an-existing-
        file question can be answered before anything is written."""
        try:
            os.makedirs(VIDEOS_DIR, exist_ok=True)
        except Exception:
            self._exp_show_status(_t("The Videos folder cannot be written to."),
                                  error=True)
            return
        try:
            pf, self._exp_progress_file = tempfile.mkstemp(
                prefix="nbvid-prog-", suffix=".txt")
            os.close(pf)
            ef, self._exp_err_file = tempfile.mkstemp(
                prefix="nbvid-err-", suffix=".txt")
            os.close(ef)
        except Exception:
            self._exp_show_status(_t("The video could not be saved."),
                                  error=True)
            return
        # Assembling the ffmpeg command probes every clip for an audio stream
        # with a blocking ffprobe call (up to SLOTS×10s on slow/hung storage),
        # so it runs on a worker thread — the same pattern as the import scan
        # (_imp_scan_worker) — and marshals the finished command back with
        # GLib.idle_add to launch the render. The card shows 'Preparing…' and
        # its controls are disabled meanwhile, so clicking Render never freezes.
        self._exp_out = out
        self._exp_segs = segs         # so a failure can name the clip that broke
        self._exp_gone = []           # media that was not on disk (set by build)
        self._exp_build_gen = getattr(self, "_exp_build_gen", 0) + 1
        gen = self._exp_build_gen
        try:
            self._exp_go.set_sensitive(False)
            self._exp_name.set_sensitive(False)
        except Exception:
            pass
        self._exp_preparing = True    # an export IS in flight from here on
        self._exp_show_status(_t("Preparing…"))
        threading.Thread(
            target=self._exp_build_worker,
            args=(gen, segs, out, self._exp_progress_file),
            daemon=True).start()

    def _exp_build_worker(self, gen, segs, out, progress_file):
        # Worker thread: the blocking ffprobe probes + command assembly. The
        # result is handed back to the main thread; nothing here touches GTK.
        try:
            cmd, total, err = self._build_ffmpeg_cmd(segs, out, progress_file)
        except Exception:
            cmd, total, err = None, 0, _t("The video could not be saved.")
        GLib.idle_add(self._exp_build_done, gen, cmd, total, err)

    def _exp_build_done(self, gen, cmd, total, err):
        # Back on the main thread. Drop the result if the dialog was closed or
        # a newer render attempt (or teardown) superseded this one.
        if gen != getattr(self, "_exp_build_gen", 0):
            return False
        # the prepare phase is over for this attempt, whatever happens below
        self._exp_preparing = False
        if getattr(self, "_exp_layer", None) is None:
            return False
        if getattr(self, "_exp_proc", None) is not None:
            return False
        if cmd is None:
            self._exp_show_status(
                    err or _t("Storyboard is empty"),
                    error=True)
            self._exp_cleanup_tmp()
            self._exp_reset_controls()
            return False
        self._exp_total = max(1, total)
        try:
            self._exp_errfh = open(self._exp_err_file, "wb")
            self._exp_proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=self._exp_errfh)
        except Exception:
            self._exp_show_status(_t("The video could not be saved."),
                                  error=True)
            self._exp_cleanup_tmp()
            self._exp_reset_controls()
            return False
        # switch the card into its rendering state (controls stay disabled)
        try:
            self._exp_prog.set_no_show_all(False)
            self._exp_prog.set_fraction(0.0)
            self._exp_prog.show()
        except Exception:
            pass
        # when the render began, so the card can say how much longer it has
        self._exp_started = GLib.get_monotonic_time()
        self._exp_show_status(_t("Saving…"))
        self._exp_poll_id = GLib.timeout_add(250, self._exp_poll)
        return False

    def _exp_reset_controls(self):
        # Re-enable the card's controls after a failed prepare so the user can
        # fix the name and try again (undoes the 'Preparing…' disable).
        try:
            self._exp_go.set_sensitive(True)
            self._exp_name.set_sensitive(True)
        except Exception:
            pass

    def _exp_poll(self):
        proc = self._exp_proc
        if proc is None:
            self._exp_poll_id = 0
            return False
        frac = self._read_progress(self._exp_progress_file, self._exp_total)
        if frac is not None:
            try:
                self._exp_prog.set_fraction(frac)
                # A render of a few minutes of footage takes minutes on this
                # hardware. A bare percentage leaves the user guessing whether
                # to wait or walk away, so say roughly how long is left.
                msg = "%s  %d%%" % (_t("Saving…"), int(frac * 100))
                left = self._exp_eta(frac)
                if left:
                    msg = "%s  ·  %s" % (msg, left)
                self._exp_show_status(msg)
            except Exception:
                pass
        if proc.poll() is None:
            return True      # still rendering — keep polling
        self._exp_poll_id = 0
        self._exp_finish(proc.returncode)
        return False

    def _exp_eta(self, frac):
        """How much longer the render has to run, in plain words — or "" while
        it is still too early to say anything honest."""
        start = getattr(self, "_exp_started", 0)
        if not start or frac <= 0.02:
            return ""
        elapsed = (GLib.get_monotonic_time() - start) / 1000000.0
        if elapsed < 3.0:
            return ""              # too little to extrapolate from
        remain = elapsed * (1.0 - frac) / frac
        if remain < 60:
            return _t("less than a minute left")
        mins = int(round(remain / 60.0))
        return _t("about %d minute%s left") % (mins, "" if mins == 1 else "s")

    def _read_progress(self, path, total):
        """Fraction 0..1 parsed from ffmpeg's -progress stream (out_time_us),
        or None if nothing is readable yet."""
        if not path:
            return None
        try:
            with open(path, errors="replace") as fh:
                txt = fh.read()
        except Exception:
            return None
        last = None
        for line in txt.splitlines():
            if line.startswith("out_time_us="):
                v = line.split("=", 1)[1].strip()
                if v.lstrip("-").isdigit():
                    last = int(v)
        if last is None:
            return None
        secs = last / 1000000.0
        if total <= 0:
            return None
        return max(0.0, min(1.0, secs / float(total)))

    def _exp_failure_reason(self):
        """Why the render failed, as one plain sentence naming the cause.

        The renderer's own last stderr line must never be shown — "Error while
        opening encoder for output stream #0:0" tells a person nothing. But the
        old message went the other way and GUESSED ("check there is free space"),
        which is worse: it names a cause that is usually not the cause. So read
        the stderr, recognise the causes that have a plain name, and otherwise
        say only what is certainly true."""
        txt = ""
        p = getattr(self, "_exp_err_file", None)
        try:
            if p and os.path.isfile(p):
                with open(p, errors="replace") as fh:
                    txt = fh.read()[-16000:]
        except Exception:
            txt = ""
        low = txt.lower()
        if "no space left" in low or "disk full" in low:
            return _t("The disk is full.")
        if "permission denied" in low or "read-only file system" in low:
            return _t("The Videos folder cannot be written to.")
        if "no such file or directory" in low or "invalid data found" in low:
            for s in getattr(self, "_exp_segs", []) or []:
                media = self._clip_media(s)
                path = media.get("path") if media else None
                if path and (not os.path.isfile(path) or path in txt):
                    return _t("This file could not be read: %s") % (
                        media.get("name") or os.path.basename(path))
        return _t("The video could not be saved.")

    def _exp_finish(self, ret):
        try:
            if self._exp_errfh is not None:
                self._exp_errfh.close()
        except Exception:
            pass
        self._exp_errfh = None
        out = getattr(self, "_exp_out", None)
        ok = (ret == 0 and out and os.path.isfile(out)
              and os.path.getsize(out) > 0)
        if ok:
            try:
                self._exp_prog.set_fraction(1.0)
            except Exception:
                pass
            rel = os.path.relpath(out, HOME) if out.startswith(HOME) else out
            msg = "Saved  ·  " + rel
            # A film with silent black gaps in it, handed back as "Saved", is a
            # lie. Name the files that were not there.
            gone = getattr(self, "_exp_gone", None)
            self._exp_show_status(msg)
            if gone:
                self._exp_show_status(
                    msg + "\n" + _t("Missing file: %s") % ", ".join(gone),
                    error=False)
            self._exp_done = True
            try:
                self._exp_go.set_label(_t("Show in Finder"))
                self._exp_go.set_sensitive(True)
                self._exp_cancel.set_label(_t("Done"))
            except Exception:
                pass
        else:
            self._exp_show_status(self._exp_failure_reason(), error=True)
            # a half-written .mp4 in the Videos folder is worse than nothing:
            # it looks like a saved film and plays as a broken one
            self._discard_partial_export()
            try:
                self._exp_go.set_sensitive(True)
                self._exp_name.set_sensitive(True)
            except Exception:
                pass
        self._exp_cleanup_tmp()
        self._exp_proc = None

    def _exp_show_status(self, text, error=False):
        try:
            self._exp_status.set_no_show_all(False)
            if error:
                self._exp_status.set_markup(
                    '<span foreground="%s">%s</span>'
                    % (RED, GLib.markup_escape_text(text)))
            else:
                self._exp_status.set_text(text)
            self._exp_status.show()
        except Exception:
            pass

    def _reveal_videos(self):
        try:
            os.makedirs(VIDEOS_DIR, exist_ok=True)
            subprocess.Popen(
                ["python3", os.path.join(DE_DIR, "finder.py"), "Videos"],
                env=dict(os.environ, PYTHONPATH=DE_DIR))
        except Exception:
            pass
        self._close_export()

    def _discard_partial_export(self):
        """Remove the unfinished .mp4 a stopped or failed render left behind.
        Only ever the file THIS render was writing, and never once it has
        finished successfully (_exp_done)."""
        out = getattr(self, "_exp_out", None)
        if not out or getattr(self, "_exp_done", False):
            return
        try:
            if os.path.isfile(out):
                os.remove(out)
        except OSError:
            pass

    def _exp_cleanup_tmp(self):
        for attr in ("_exp_progress_file", "_exp_err_file"):
            p = getattr(self, attr, None)
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
            setattr(self, attr, None)
        # drop the generated caption / title-card PNGs from this render
        for p in getattr(self, "_exp_tmp_imgs", []) or []:
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass
        self._exp_tmp_imgs = []

    def _export_teardown(self):
        """Stop any in-flight render and drop its scratch files. Safe to call
        repeatedly (close, cancel, destroy)."""
        # Supersede any pending async command-build so its GLib.idle_add
        # callback is dropped instead of launching a render into a torn-down
        # (or reopened) dialog.
        self._exp_build_gen = getattr(self, "_exp_build_gen", 0) + 1
        self._exp_preparing = False
        pid = getattr(self, "_exp_poll_id", 0)
        if pid:
            try:
                GLib.source_remove(pid)
            except Exception:
                pass
            self._exp_poll_id = 0
        proc = getattr(self, "_exp_proc", None)
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=3)   # let it release the file before we look
            except Exception:
                pass
            # A cancelled render used to leave its half-written .mp4 sitting in
            # the Videos folder, indistinguishable from a finished film until
            # the user tried to play it. Take it away with the render.
            self._discard_partial_export()
        self._exp_proc = None
        fh = getattr(self, "_exp_errfh", None)
        if fh is not None:
            try:
                fh.close()
            except Exception:
                pass
            self._exp_errfh = None
        self._exp_cleanup_tmp()

    def _exp_busy(self):
        """True while an export the user asked for is in flight. That starts at
        the click, not at the ffmpeg process: assembling the command probes every
        clip with a blocking ffprobe on a worker thread, and on slow storage the
        card can sit on 'Preparing…' for seconds. Guarding only _exp_proc let a
        stray click or Esc in that window throw the export away without a word —
        the very thing the guard exists to prevent."""
        return (getattr(self, "_exp_proc", None) is not None
                or bool(getattr(self, "_exp_preparing", False)))

    def _exp_scrim_press(self, *_):
        # A click outside the card dismisses the export dialog — but never mid-
        # render, where a stray click would silently kill a long render. While a
        # render is running the explicit Cancel button is the only way to abort.
        if self._exp_busy():
            return True
        self._close_export()
        return True

    def _close_export(self):
        self._export_teardown()
        layer = getattr(self, "_exp_layer", None)
        if layer is not None:
            try:
                self._overlay.remove(layer)
            except Exception:
                pass
            self._exp_layer = None
            return True
        return False

    # ================= confirm overlay =================
    def _confirm(self, title, body, ok_label, on_yes):
        """House-style in-window confirm: a scrim over a warm-paper card with a
        serif title, a body line, and Cancel / <signage-red action> buttons.
        Guards destructive actions (e.g. New Project over a built storyboard)."""
        self._close_confirm()
        try:
            self._close_menu()
        except Exception:
            pass
        # Size the scrim + centre the card off the LIVE window allocation,
        # falling back to the real primary-monitor size — NEVER a hardcoded
        # 1920x1080. max(alloc, 1920) overflowed the scrim and dropped the card
        # off a smaller native panel.
        _sw, _sh = nbapp.screen_size()
        try:
            alloc = self.get_allocation()
            W = alloc.width if alloc.width > 1 else _sw
            H = alloc.height if alloc.height > 1 else _sh
        except Exception:
            W, H = _sw, _sh
        layer = Gtk.Fixed()
        scrim = Gtk.EventBox()
        scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        scrim.set_size_request(W, H)
        scrim.connect("button-press-event",
                      lambda *a: (self._close_confirm(), True)[1])
        layer.put(scrim, 0, 0)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.get_style_context().add_class("impcard")
        card.set_size_request(430, -1)
        tl = Gtk.Label(label=title, xalign=0)
        tl.get_style_context().add_class("dlgtitle")
        card.pack_start(tl, False, False, 0)
        bd = Gtk.Label(label=body, xalign=0)
        bd.get_style_context().add_class("impsub")
        bd.set_line_wrap(True)
        bd.set_max_width_chars(42)
        card.pack_start(bd, False, False, 0)
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        footer.set_margin_top(16)
        footer.pack_start(Gtk.Box(), True, True, 0)
        cancel = Gtk.Button(label=_t("Cancel"))
        cancel.set_relief(Gtk.ReliefStyle.NONE)
        cancel.get_style_context().add_class("impbtn")
        cancel.connect("clicked", lambda *_: self._close_confirm())
        footer.pack_start(cancel, False, False, 0)
        ok = Gtk.Button(label=ok_label)
        ok.set_relief(Gtk.ReliefStyle.NONE)
        ok.get_style_context().add_class("dlgok")
        ok.connect("clicked", lambda *_: (self._close_confirm(), on_yes()))
        footer.pack_start(ok, False, False, 0)
        card.pack_start(footer, False, False, 0)

        holder = Gtk.EventBox()   # own GdkWindow so it blits over the app body
        holder.add(card)
        layer.put(holder, 0, 0)
        self._overlay.add_overlay(layer)
        layer.show_all()
        try:
            _min, nat = holder.get_preferred_size()
            cw = nat.width if nat.width > 1 else 430
            ch = nat.height if nat.height > 1 else 200
            layer.move(holder, max((W - cw) // 2, 0), max((H - ch) // 2, 0))
        except Exception:
            pass
        self._confirm_layer = layer

    def _close_confirm(self):
        layer = getattr(self, "_confirm_layer", None)
        if layer is not None:
            try:
                self._overlay.remove(layer)
            except Exception:
                pass
            self._confirm_layer = None
            return True
        return False

    def _on_key(self, widget, ev):
        # Esc dismisses this app's own overlays (confirm / export / import)
        # before falling through to the base handler (About, menu, then close),
        # so a dialog is never skipped straight to closing the whole app.
        if ev.keyval == Gdk.KEY_Escape:
            if self._close_confirm():
                return True
            if getattr(self, "_exp_layer", None) is not None:
                # Don't let a stray Esc silently kill an in-flight render; while
                # rendering, only the explicit Cancel button aborts it.
                if self._exp_busy():
                    return True
                self._close_export()
                return True
            if self._close_import():
                return True
        # Ctrl+Z takes the last change back, Ctrl+Shift+Z (or Ctrl+Y) puts it
        # again. Held back while an overlay is up so a dialog's own keys win.
        if (ev.state & Gdk.ModifierType.CONTROL_MASK
                and self._confirm_layer is None
                and getattr(self, "_exp_layer", None) is None
                and getattr(self, "_imp_layer", None) is None):
            if ev.keyval in (Gdk.KEY_z, Gdk.KEY_Z):
                if ev.state & Gdk.ModifierType.SHIFT_MASK:
                    self._redo_action()
                else:
                    self._undo_action()
                return True
            if ev.keyval in (Gdk.KEY_y, Gdk.KEY_Y):
                self._redo_action()
                return True
        return super()._on_key(widget, ev)

    # ================= clip menu actions =================
    def _delete_clip_guarded(self):
        """Remove the selected clip, but confirm first when it carries edits
        worth losing (a title, a transition, or a hand-set duration). A plain
        default clip — one click to place again — is removed without nagging,
        matching the house rule of guarding destructive, no-undo actions."""
        k = self._sel_cell
        if k is None or not (0 <= k < len(self.clips)):
            return
        clip = self.clips[k]
        media = self._clip_media(clip)
        # A clip carrying any hand edit is worth a confirm before it's gone.
        edited = (bool(clip.get("title")) or bool(clip.get("transition"))
                  or clip.get("effect", "none") != "none"
                  or clip.get("kenburns", "none") != "none"
                  or float(clip.get("speed", 1.0)) != 1.0
                  or float(clip.get("start", 0.0)) != 0.0
                  or clip.get("kind") == "title")
        self._menu_delete()

    def _menu_delete(self):
        k = self._sel_cell
        if k is None or not (0 <= k < len(self.clips)):
            return
        self._stop_playback(reset=True)
        self._push_undo(name="Delete Clip")
        self.clips.pop(k)
        self._sel_cell = None
        self._save_project()
        self._render_all()
        self._active_transition = None
        self._highlight_palette(None)
        self._load_props(None)

    def _menu_split(self):
        # Split the selected clip into two: the first half keeps its lead-in
        # transition, the second half picks up the source AFTER the split point
        # (so a trimmed video's two halves stay continuous). Inserts in place —
        # the sequence has no fixed length.
        k = self._sel_cell
        if k is None or not (0 <= k < len(self.clips)):
            return
        clip = self.clips[k]
        dur = self._clip_dur(clip)
        if dur < 2:
            return                     # too short to split meaningfully
        self._stop_playback(reset=True)
        self._push_undo(name="Split Clip")
        half = dur // 2
        second = dict(clip)
        second["duration"] = dur - half
        second["transition"] = None
        if clip.get("kind") in ("video", "audio"):
            second["start"] = (float(clip.get("start", 0.0))
                               + half * float(clip.get("speed", 1.0) or 1.0))
        clip["duration"] = half
        self.clips.insert(k + 1, second)
        self._save_project()
        self._render_all()
        self._select_cell(k + 1)

    def _menu_add_transition(self):
        k = self._sel_cell
        if k is None or not (0 <= k < len(self.clips)):
            return
        key = self._active_transition or "trfade"
        self._push_undo(name="Add Transition")
        self._active_transition = key
        self._highlight_palette(key)
        self.clips[k]["transition"] = key
        try:
            self._prop_trans.set_text(_t(TRANS_NAME.get(key) or "None"))
        except Exception:
            pass
        self._render_story()
        self._render_timeline()
        self._save_project()

    def _menu_add_title(self):
        """Insert a standalone title card after the selection (or at the end)."""
        at = (self._sel_cell + 1) if isinstance(self._sel_cell, int) \
            else len(self.clips)
        self._insert_clip(_new_title("Title", ""), at, "Add Title Card")

    def _menu_add_credits(self):
        """Append an end-credits card to the very end of the movie."""
        self._insert_clip(_new_title("The End", ""), len(self.clips),
                          "Add Credits")

    # ================= undo / redo =================
    # Editing a movie is a long sequence of small, easily-regretted changes: a
    # clip dragged out of order, a length typed wrong, a split in the wrong
    # place, a project cleared by File > New. None of it could be taken back.
    # A step is just the serialised project (the same small dict the autosave
    # writes) plus what was selected, so recording one is nearly free and
    # restoring one goes through the same validated _apply_data as loading a
    # file — no separate "inverse operation" per action to get wrong.

    def _snapshot(self, key=None):
        return (key, self._serialize(), self._sel_cell, self._sel_music,
                self._path)

    def _push_undo(self, key=None, name=None):
        """Record the project as it is NOW, before the caller changes it.

        `key` coalesces a run of continuous edits into one step: dragging the
        volume slider fires value-changed dozens of times, and each one would
        otherwise be its own undo step, so Ctrl+Z would crawl back through the
        drag. A key of None never coalesces (discrete actions)."""
        if self._undo_busy:
            return
        try:
            if key is not None and self._undo and self._undo[-1][0] == key:
                return
            self._undo.append(self._snapshot(key))
            self._undo_names.append(name)
            del self._undo[:-UNDO_DEPTH]
            del self._undo_names[:-UNDO_DEPTH]
            self._redo = []          # a fresh edit forks the history
            self._redo_names = []
        except Exception:
            pass

    def _restore(self, snap):
        """Put a recorded project back on screen, selection and all."""
        _key, data, sel, selmus, path = snap
        self._undo_busy = True
        try:
            self._stop_playback(reset=True)
            self._pv_teardown()
            self._apply_data(data)
            self._path = path
            self._sel_music = bool(selmus) and self.music is not None
            self._sel_cell = (sel if isinstance(sel, int)
                              and 0 <= sel < len(self.clips) else None)
            clip = self._sel_clip()
            self._active_transition = clip.get("transition") if clip else None
            self._render_all()
            self._highlight_palette(self._active_transition)
            self._load_props(clip)
            self._update_project_name()
        except Exception:
            pass
        finally:
            self._undo_busy = False
        self._save_project()

    def _undo_action(self):
        if not self._undo:
            return
        try:
            self._redo.append(self._snapshot())
            self._redo_names.append(
                self._undo_names.pop() if self._undo_names else None)
            del self._redo[:-UNDO_DEPTH]
            del self._redo_names[:-UNDO_DEPTH]
            self._restore(self._undo.pop())
        except Exception:
            pass

    def _redo_action(self):
        if not self._redo:
            return
        try:
            self._undo.append(self._snapshot())
            self._undo_names.append(
                self._redo_names.pop() if self._redo_names else None)
            del self._undo[:-UNDO_DEPTH]
            del self._undo_names[:-UNDO_DEPTH]
            self._restore(self._redo.pop())
        except Exception:
            pass

    # ================= persistence =================
    def _serialize(self):
        """The whole project (media bin + clip sequence + music) as a plain
        dict — shared by the autosave and File ▸ Save."""
        return {
            "version": 2,
            "bin": [{"path": m["path"], "name": m["name"], "kind": m["kind"],
                     "dur": m["dur"], "srcdur": m.get("srcdur", 0.0)}
                    for m in self._bin],
            "clips": [self._clip_record(c) for c in self.clips],
            "music": (dict(self.music) if isinstance(self.music, dict)
                      else None),
            "size": [self._out_w, self._out_h],
        }

    def _clip_record(self, c):
        """A clip as a plain persistable dict (every Movie-Maker attribute)."""
        return {
            "media": c.get("media"), "kind": c.get("kind", "video"),
            "start": float(c.get("start", 0.0) or 0.0),
            "duration": self._clip_dur(c),
            "title": c.get("title", "") or "",
            "transition": c.get("transition"),
            "effect": c.get("effect", "none"),
            "volume": float(c.get("volume", 1.0)),
            "mute": bool(c.get("mute", False)),
            "afade": bool(c.get("afade", False)),
            "vfade": bool(c.get("vfade", False)),
            "kenburns": c.get("kenburns", "none"),
            "speed": float(c.get("speed", 1.0)),
            "cardtext": c.get("cardtext", ""), "cardsub": c.get("cardsub", ""),
        }

    def _sanitize_clip(self, s):
        """Validate a raw clip dict into the in-memory model, or None to drop.
        A media clip must resolve to a real bin item; a title card needs none."""
        if not isinstance(s, dict):
            return None
        kind = s.get("kind")
        if kind == "title":
            c = _new_title(str(s.get("cardtext", "") or "Title"),
                           str(s.get("cardsub", "") or ""))
        else:
            try:
                mi = int(s.get("media"))
            except (TypeError, ValueError):
                return None
            if mi < 0 or mi >= len(self._bin):
                return None            # dangling reference — drop the clip
            c = _new_clip(mi, self._bin[mi]["kind"], self._bin[mi]["dur"])
        try:
            c["duration"] = max(1, min(3600, int(round(float(
                s.get("duration", c["duration"]))))))
        except Exception:
            pass
        try:
            c["start"] = max(0.0, float(s.get("start", 0.0) or 0.0))
        except Exception:
            c["start"] = 0.0
        c["title"] = str(s.get("title", "") or "")
        tr = s.get("transition")
        c["transition"] = tr if tr in TRANS_KEYS else None
        ef = s.get("effect")
        c["effect"] = ef if ef in EFFECT_KEYS else "none"
        try:
            c["volume"] = max(0.0, min(2.0, float(s.get("volume", 1.0))))
        except Exception:
            c["volume"] = 1.0
        c["mute"] = bool(s.get("mute", False))
        c["afade"] = bool(s.get("afade", False))
        c["vfade"] = bool(s.get("vfade", c.get("vfade", False)))
        kb = s.get("kenburns")
        c["kenburns"] = kb if kb in KENBURNS_KEYS else "none"
        try:
            sp = float(s.get("speed", 1.0))
        except Exception:
            sp = 1.0
        c["speed"] = sp if sp in SPEED_VALUES else 1.0
        return c

    def _apply_data(self, data):
        """Replace the in-memory project with `data`, validating and dropping
        anything malformed. Understands both the v2 `clips` schema and the
        original v1 sparse `slots` (migrated in order). Empty/invalid data
        yields a blank project (no seed)."""
        self._bin = []
        self.clips = []
        self.music = None
        self._out_w, self._out_h = EXPORT_W, EXPORT_H
        if not isinstance(data, dict):
            return
        # remembered export frame size; anything not on the offered list is
        # ignored rather than trusted (it feeds ffmpeg's scale/pad geometry)
        try:
            sw, sh = data.get("size") or (0, 0)
            if (int(sw), int(sh)) in EXPORT_SIZES:
                self._out_w, self._out_h = int(sw), int(sh)
        except Exception:
            pass
        binlist = data.get("bin")
        if isinstance(binlist, list):
            for m in binlist:
                if not isinstance(m, dict):
                    continue
                path = str(m.get("path", ""))
                name = str(m.get("name", "")) or os.path.basename(path)
                kind = m.get("kind")
                kind = kind if kind in KIND_ICON else "video"
                try:
                    dur = int(m.get("dur", KIND_DUR.get(kind, 4)))
                except Exception:
                    dur = KIND_DUR.get(kind, 4)
                try:
                    srcdur = float(m.get("srcdur", 0.0) or 0.0)
                except Exception:
                    srcdur = 0.0
                self._bin.append({"path": path, "name": name, "kind": kind,
                                  "dur": max(1, min(3600, dur)),
                                  "srcdur": srcdur})
        # v2 clips list, else migrate v1 sparse slots (dropping the gaps).
        raw = data.get("clips")
        if not isinstance(raw, list):
            slotlist = data.get("slots")
            raw = [s for s in slotlist if s] if isinstance(slotlist, list) else []
        for s in raw:
            c = self._sanitize_clip(s)
            if c is not None:
                self.clips.append(c)
        # background music track
        mus = data.get("music")
        if isinstance(mus, dict) and mus.get("path"):
            path = str(mus.get("path", ""))
            try:
                vol = max(0.0, min(2.0, float(mus.get("volume", 0.6))))
            except Exception:
                vol = 0.6
            self.music = {
                "path": path,
                "name": str(mus.get("name", "") or os.path.basename(path)),
                "volume": vol, "fadein": bool(mus.get("fadein", True)),
                "fadeout": bool(mus.get("fadeout", True))}

    @staticmethod
    def _is_project(data):
        """Whether `data` is recognisable as something this app wrote.

        Every store _serialize produces is a dict carrying a "bin" list and a
        "clips" list (both empty in a brand-new project, which is why an EMPTY
        list passes), and every entry in either is a dict. A v1 store carried
        "slots" instead of "clips", and a v1 slots list could hold nulls for the
        gaps, so a null there is allowed. Nothing here can misfire on our own
        file, empty or full.

        The ENTRIES are checked, not just that the containers are lists,
        because "bin": ["...", "..."] passed a container-only test: every entry
        was then dropped as not-a-dict, the project opened blank, and the
        close-time autosave wrote that blankness over the store."""
        if not isinstance(data, dict):
            return False
        binlist = data.get("bin")
        if not isinstance(binlist, list):
            return False
        if not all(isinstance(m, dict) for m in binlist):
            return False
        for key in ("clips", "slots"):
            seq = data.get(key)
            if isinstance(seq, list) and all(
                    isinstance(s, dict) or s is None for s in seq):
                return True
        return False

    def _quarantine_autosave(self):
        """Move an autosave this app cannot read as a project aside, under the
        same <name>.damaged-<timestamp> name nbapp.preserve_damaged uses.

        nbapp quarantines any store that fails to PARSE, and keeps one .bak of a
        store that does. Neither covers this: valid JSON of the wrong shape
        parses perfectly, reads as "no data", and the close-time autosave writes
        an EMPTY project over it. The .bak only survives the second open when the
        blank project weighs less than what it replaced — and a blank project is
        {"version": 2, "size": [w, h]}, three leaves, so a small damaged store
        (a bare string, or a dict with one key) outweighed nothing and the .bak
        was refreshed with blankness on open #2. That was the only copy left of
        the media bin and the storyboard. Two opens, two closes, no user action.
        Moving the file aside instead means the bytes are never a candidate to be
        overwritten in the first place."""
        try:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            dest = "%s.damaged-%s" % (PROJECT_FILE, stamp)
            n = 2
            while os.path.exists(dest):
                dest = "%s.damaged-%s-%d" % (PROJECT_FILE, stamp, n)
                n += 1
            os.replace(PROJECT_FILE, dest)
        except OSError:
            pass

    def _load_project(self):
        """Restore the working project from the autosave (empty on first run —
        no seed). An autosave that parses but is not a project shape is moved
        aside rather than opened blank and then saved over."""
        try:
            with open(PROJECT_FILE) as fh:
                data = json.load(fh)
        except Exception:
            return
        if not self._is_project(data):
            self._quarantine_autosave()
            return
        self._apply_data(data)

    def _save_project(self):
        """Autosave the working project to CFG_DIR/video.json on every mutation
        and on close (session recovery). Never lets an I/O error reach the UI."""
        try:
            nbapp.atomic_write_json(PROJECT_FILE, self._serialize())
        except Exception:
            # persistence must never crash the editor
            pass

    def _on_destroy(self, *_):
        # stop any in-flight render / preview decode (and drop their scratch
        # files) and halt playback before the final autosave, so a closing
        # window never leaves ffmpeg orphaned or a timeout firing.
        self._stop_playback(reset=False)
        # Release the pipeline BEFORE the autosave: teardown drops the bus watch
        # first, so an EOS still queued on the main loop cannot reach a handler
        # whose window is on its way out.
        if getattr(self, "_player", None) is not None:
            try:
                self._player.teardown()
            except Exception:                                  # noqa: BLE001
                pass
        self._pv_teardown()
        self._export_teardown()
        self._save_project()
        return False

    # ========= File menu: named project files ($NB_HOME/Documents) =========
    def _update_project_name(self):
        """Reflect the current named project (its file stem, or 'Untitled') in
        the Properties table's Project row."""
        try:
            if self._path:
                self._prop_vals["Project"].set_text(
                    os.path.splitext(os.path.basename(self._path))[0])
            else:
                self._prop_vals["Project"].set_text(_t("Untitled"))
        except Exception:
            pass

    def _flash(self, text):
        """Surface a transient file-op error in the Project row (crash-safe);
        the next _update_project_name() clears it."""
        try:
            self._prop_vals["Project"].set_markup(
                '<span foreground="#C8341E">%s</span>'
                % GLib.markup_escape_text(text))
        except Exception:
            pass

    def _reset_view_after_load(self):
        """Shared tail for New / Open: halt playback + any preview decode, drop
        selection + transport state, and repaint every pane against the
        freshly-loaded model."""
        self._stop_playback(reset=True)
        self._pv_teardown()
        self._sel_cell = None
        self.sel_media = None
        self._active_transition = None
        self._render_all()
        self._highlight_palette(None)
        self._load_props(None)
        self._update_project_name()

    def _write_file(self, path):
        """Serialise the project to `path`. Returns True on success."""
        try:
            nbapp.atomic_write_json(path, self._serialize(),
                                    ensure_ascii=False, indent=2)
            return True
        except Exception as exc:
            self._last_save_error = exc
            return False

    def _open_file(self, path):
        """Load a named project file into the editor. Returns True on success.

        Hardened: a Video Editor project must be a dict carrying a project
        marker (a 'slots' or 'bin' list). Without it we refuse the file rather
        than clobbering an unrelated JSON document with an empty project."""
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            # ONE definition of "is this ours" (_is_project), shared with the
            # autosave. The marker used to be satisfied by ANY ONE of bin /
            # clips / slots being a list, so a foreign JSON document with a
            # "clips" key was adopted: the storyboard went blank, the autosave
            # wrote that blankness into session recovery, and _path pointed at
            # the user's file so the next Save clobbered that too.
            if not self._is_project(data):
                raise ValueError("not a Video Editor project")
        except Exception:
            self._flash(_t("Not a Video project"))
            return False
        self._push_undo(name="Open Project…")
        self._apply_data(data)
        self._path = path
        self._save_project()   # session recovery adopts the opened project
        self._reset_view_after_load()
        return True

    def _project_nonempty(self):
        """True when the working project holds media or a placed clip — i.e.
        when New / discard has something to actually throw away."""
        return bool(self._bin) or bool(self.clips) or bool(self.music)

    def _file_new(self):
        """Start an empty project — no media, empty storyboard. Confirms first
        when the current project isn't empty (a destructive one-click action);
        leaves any saved files on disk untouched."""
        self._do_file_new()

    def _do_file_new(self):
        # recorded, so an accidental New Project is one Ctrl+Z away
        self._push_undo(name="New Project")
        self._apply_data({})
        self._path = None
        self._save_project()
        self._reset_view_after_load()

    def _file_open(self):
        path = self._choose_file(save=False)
        if path and os.path.isfile(path):
            self._open_file(path)

    def _file_save(self):
        """Write to the current project file; prompt via Save As if there is
        none."""
        if not self._path:
            return self._file_save_as()
        if self._write_file(self._path):
            self._update_project_name()
        else:
            reason = nbapp.save_failure_reason(
                getattr(self, "_last_save_error", None), self._path)
            if getattr(getattr(self, "_last_save_error", None), "errno", None) == 28:
                reason += " " + _t("Free up space and try again.")
            self._flash(reason)

    def _file_save_as(self):
        path = self._choose_file(save=True)
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += ".json"
        self._path = path
        self._file_save()

    def _choose_file(self, save):
        """Finder-style in-app picker under Documents; return a path or None."""
        try:
            os.makedirs(PROJ_DIR, exist_ok=True)
        except Exception:
            pass
        base = os.path.dirname(self._path) if self._path else PROJ_DIR
        start = base if os.path.isdir(base) else PROJ_DIR
        if save:
            suggested = (os.path.basename(self._path) if self._path
                         else "project.json")
            return nbpicker.save_file(self, title="Save Project As",
                                      start_dir=start, suggested_name=suggested,
                                      patterns=("*.json",), default_ext=".json")
        return nbpicker.open_file(self, title="Open Project",
                                  start_dir=start, patterns=("*.json",))

    # ================= menus =================
    def _menu_set_view(self, which):
        # Crash-safe: the timeline stack is built in __init__, but the menu
        # can only be clicked after the window is up, so guard defensively.
        if getattr(self, "tl_stack", None) is not None:
            self._set_view(which)

    def menu_items(self, name):
        if name == "File":
            # Real, wired project I/O to $NB_HOME/Documents. Open covers
            # both bringing in media and loading a saved project.
            return [
                ("New Project", self._file_new),
                ("Open Project…", self._file_open),
                ("Import Media…", lambda: self._open_import()),
                ("Add Music…", lambda: self._add_music()),
                nbapp.SEP,
                ("Save Project", self._file_save),
                ("Save Project As…", self._file_save_as),
                nbapp.SEP,
                ("Export Video…", lambda: self._open_export()),
                nbapp.SEP,
                ("Close    Esc", self.close),
            ]
        if name == "Edit":
            # Undo/Redo ahead of the inherited text-field actions; each is
            # offered only when there is something to take back.
            # the SHARED builder every other editor uses, so all four word
            # Undo/Redo identically and each entry names the action it takes
            # back ("Undo Delete Clip").
            return nbapp.undo_menu_items(self.history) + [
                nbapp.SEP,
            ] + super().menu_items(name)
        if name == "View":
            # Real toggle: switch the bottom strip between its two views.
            return [
                ("Storyboard", lambda: self._menu_set_view("story")),
                ("Timeline", lambda: self._menu_set_view("time")),
            ]
        if name == "Clip":
            # The per-clip actions light up only once a clip is selected (so
            # they never make an empty promise). Insert actions always work.
            k = self._sel_cell
            has_clip = (not self._sel_music and k is not None
                        and 0 <= k < len(self.clips))
            can_split = has_clip and self._clip_dur(self.clips[k]) >= 2
            can_left = has_clip and k > 0
            can_right = has_clip and k < len(self.clips) - 1
            return [
                ("Import Media…", lambda: self._open_import()),
                ("Add Title Card", lambda: self._menu_add_title()),
                ("Add Credits", lambda: self._menu_add_credits()),
                ("Add Music…", lambda: self._add_music()),
                nbapp.SEP,
                ("Split Clip", (lambda: self._menu_split()) if can_split
                 else None),
                ("Move Left", (lambda: self._move_clip(-1)) if can_left
                 else None),
                ("Move Right", (lambda: self._move_clip(1)) if can_right
                 else None),
                ("Delete Clip…",
                 (lambda: self._delete_clip_guarded()) if has_clip else None),
                nbapp.SEP,
                ("Add Transition",
                 (lambda: self._menu_add_transition()) if has_clip else None),
            ]
        return super().menu_items(name)

    def _add_music(self):
        """Choose a Home-folder audio file as the movie's background track."""
        self._stop_playback(reset=True)
        try:
            self._close_menu()
        except Exception:
            pass
        start = HOME
        for d in ("Music", "Documents"):
            p = os.path.join(HOME, d)
            if os.path.isdir(p):
                start = p
                break
        path = nbpicker.open_file(
            self, title="Add Background Music", start_dir=start,
            patterns=tuple("*" + e for e in sorted(AUDIO_EXT)))
        if not path or not os.path.isfile(path):
            return
        self._push_undo(name="Add Music…")
        self.music = {"path": path, "name": os.path.basename(path),
                      "volume": 0.6, "fadein": True, "fadeout": True}
        self._save_project()
        self._render_timeline()
        self._select_music()

    # ================= css =================
    def _install_css(self):
        css = b"""
        * { font-family: "Nimbus Sans","Helvetica",sans-serif; }

        /* ---------- media bin (left panel) ---------- */
        .mediabin { background: #F1EEE6; border-right: 1px solid #C9C4B6; }
        .binhead { padding: 18px 20px 16px; border-bottom: 1px solid #D7D2C5; }
        .bintitle { font-size: 11px; letter-spacing: 0.16em; color: #6E695E;
                    font-weight: 700; }
        .importbtn { min-height: 30px; padding: 0 13px; border: 1px solid #C9C4B6;
                     background: #FCFBF8; border-radius: 8px; box-shadow: none;
                     font-size: 12px; font-weight: 600; color: #1A1916; }
        .importbtn:hover { background: #F4F2EC; }
        .emptytitle { font-size: 14px; font-weight: 600; color: #6E695E; }
        .emptysub { font-size: 12px; color: #9A9484; }

        .binscroll, .binscroll viewport { background: #F1EEE6; }
        .binrow { padding: 10px 14px; border-bottom: 1px solid #D7D2C5; }
        .binrow.binsel { background: #EAE3D2; box-shadow: inset 3px 0 0 #C8341E; }
        .videohit { padding: 0; border: none; background: transparent;
                    background-image: none; box-shadow: none; }
        .binhit:hover .binrow { background: #F8F7F2; }
        .binname { font-size: 13px; color: #1A1916; font-weight: 600; }
        .binmeta { font-size: 11px; color: #9A9484; }

        .transwrap { border-top: 1px solid #D7D2C5; padding: 18px 20px 20px; }
        .translabel { font-size: 10px; letter-spacing: 0.16em; color: #9A9484;
                      font-weight: 700; margin-bottom: 12px; }
        .transcell { border: 1px solid #D7D2C5; background: #FCFBF8;
                     border-radius: 8px; padding: 10px 4px 8px; }
        .transcell.transel { border-color: #C8341E; background: #EAE3D2; }
        .transhit:hover .transcell { border-color: #C8341E; }
        .transname { font-size: 10px; color: #6E695E; }

        /* ---------- preview (centre) ---------- */
        .previewcol { background: #FCFBF8; }
        .screen { background: #1A1916; border-radius: 8px; }
        /* The screen sits inside a scroller (see _preview), whose viewport would
           otherwise paint a paper rectangle over the black stage, and which
           clips its child - so the stage's drop shadow lives out here on the
           wrapper rather than on .screen where it would be cut off. */
        .prevframe, .prevframe viewport { background-color: #1A1916; }
        .prevframe { border-radius: 8px;
                     box-shadow: 4px 4px 0 rgba(26,25,22,0.14); }
        .noprev { font-size: 13px; color: #8A857A; }
        .prevsub { font-size: 12px; color: #8A857A; letter-spacing: 0.04em; }
        .roundbtn { border: 1px solid #C9C4B6; background: #F4F2EC;
                    background-image: none; box-shadow: none; padding: 0;
                    border-radius: 50%; }
        .timecode { font-size: 13px; color: #6E695E; letter-spacing: 0.02em; }

        /* ---------- properties (right panel) ---------- */
        .propcol { background: #F1EEE6; border-left: 1px solid #C9C4B6;
                   padding: 24px; }
        .propscroll, .propscroll viewport { background: #F1EEE6; }
        /* padding-top clears the accents on an uppercase heading. Without it
           the card's top rule slices the acutes off PROPRIETES in French,
           Spanish and Serbian, which reads as a typo rather than a clip; the
           English PROPERTIES has no ascender there so it never showed. */
        .prophead { font-size: 11px; letter-spacing: 0.16em; color: #6E695E;
                    font-weight: 700; padding-top: 4px; margin-bottom: 14px; }
        .prophint { font-size: 13px; color: #9A9484; }
        .propname { font-size: 15px; color: #1A1916; font-weight: 600; }
        .propfieldlabel { font-size: 10px; letter-spacing: 0.14em;
                          color: #9A9484; font-weight: 700; }
        .propentry { background: #FCFBF8; border: 1px solid #C9C4B6;
                     border-radius: 8px; padding: 6px 9px; font-size: 14px;
                     color: #1A1916; box-shadow: none; }
        .propentry:focus { border: 1px solid #B3AD9E; box-shadow: none; }
        .propremove { padding: 6px 12px; border: 1px solid #C9C4B6;
                      border-radius: 8px; background: #FCFBF8; color: #C8341E;
                      font-size: 12px; font-weight: 600; box-shadow: none; }
        .propremove:hover { background: #F1EEE6; border-color: #C8341E; }
        /* pinned to the bottom of the column, so the hairline is a footer rule
           rather than a divider inside a scrolling list */
        .proptable { border-top: 1px solid #D7D2C5; margin-top: 12px;
                     padding-top: 8px; }
        .proprow { padding: 9px 0; }
        .propkey { font-size: 13px; color: #6E695E; }
        .propval { font-size: 13px; color: #1A1916; font-weight: 500; }

        /* ---------- timeline (bottom) ---------- */
        .timeline { background: #F1EEE6; border-top: 1px solid #C9C4B6; }
        .tlbar { padding: 12px 20px; border-bottom: 1px solid #D7D2C5; }
        .seg { border: 1px solid #C9C4B6; border-radius: 8px; }
        .segbtn { padding: 7px 16px; font-size: 13px; font-weight: 600;
                  color: #6E695E; background: #FCFBF8; border-radius: 0;
                  border: none; box-shadow: none; }
        .segbtn:hover { color: #1A1916; background: #F4F2EC; }
        /* Active = the shared selection fill plus an accent EDGE, never a
           field of red. Horizontal segments get the edge along the bottom,
           which is the same signal the vertical sidebars carry down their
           left side. Red stays reserved for "this destroys something". */
        .segbtn.active { color: #1A1916; background: #EAE3D2;
                         box-shadow: inset 0 -3px 0 #C8341E; }
        .segbtn.active:hover { color: #1A1916; background: #EAE3D2;
                               box-shadow: inset 0 -3px 0 #C8341E; }
        /* These are real buttons, so the theme would otherwise add its own
           padding, minimum size and pressed shadow on top of the 28x28
           request and the control would grow. Zeroing those leaves the
           border, fill and radius below as the entire appearance: the
           chrome here is size-neutral. The focus ring is deliberately NOT
           touched, as it is what shows Tab has landed on the control. */
        .squarebtn { border: 1px solid #C9C4B6; background: #F4F2EC;
                     border-radius: 8px; padding: 0; margin: 0;
                     min-width: 0; min-height: 0; box-shadow: none;
                     background-image: none; }
        .squarebtn:hover { background: #EAE3D2; border-color: #B3AD9E;
                           box-shadow: none; background-image: none; }
        .squarebtn:active, .squarebtn:checked {
                     background: #EAE3D2; border-color: #B3AD9E;
                     box-shadow: none; background-image: none; }
        .squarebtn:disabled { background: #F4F2EC; border-color: #D7D2C5;
                     box-shadow: none; background-image: none; }

        .storyscroll, .storyscroll viewport { background: #F1EEE6; }
        .storyrow { padding: 0 24px; }
        .storycell { border: 1px dashed #C9C4B6; border-radius: 12px;
                     background: #FCFBF8; }
        .storycell.storyfilled { border-style: solid; border-color: #C9C4B6;
                     background: #FCFBF8; }
        .storycell.storysel { border-style: solid; border-color: #C8341E;
                     background: #EAE3D2; }
        .storyhit:hover .storycell { border-color: #C8341E; }
        /* the clip's own frame, on a dark mat so a letterboxed or still-
           decoding picture reads as a screen rather than a hole in the card */
        .storythumb { background: #1A1916; border: 1px solid #D7D2C5;
                      margin-bottom: 2px; }
        .storymattext { font-size: 11px; color: #F1EEE6; padding: 0 6px; }
        .storynum { font-size: 12px; color: #9A9484; font-weight: 600; }
        .storyhint { font-size: 11px; color: #7D7767; }
        .storyname { font-size: 12px; color: #1A1916; font-weight: 600; }
        .storymeta { font-size: 11px; color: #6E695E; }
        .storytitleovl { font-size: 10px; color: #1A1916; }
        .transdot { border: 1px dashed #C9C4B6; border-radius: 50%; }
        /* a clip that HAS a transition: ink, like every other "this is set"
           mark here. The accent belongs to the selection alone. */
        .transdot.transdotset { border-style: solid; border-color: #1A1916;
                    background: #EFEBE0; }
        .transplus { color: #9A9484; font-size: 15px; }

        .ruler { border-bottom: 1px solid #D7D2C5; }
        .rulergutter { border-right: 1px solid #D7D2C5; }
        .tick { font-size: 10px; color: #9A9484; border-left: 1px solid #D7D2C5;
                padding-left: 6px; }
        /* the playhead is a POSITION, not a selection: ink, so the one
           signage red on this window keeps meaning SELECTED (the chosen clip,
           bin row, transition and view). It is on screen whether or not
           anything is playing. */
        .playhead { background: #1A1916; }
        .track { border-bottom: 1px solid #D7D2C5; }
        .tracklabel { border-right: 1px solid #D7D2C5; font-size: 11px;
                      letter-spacing: 0.12em; color: #6E695E; font-weight: 700;
                      padding: 0 12px; }
        .tlchip { background: #FCFBF8; border: 1px solid #C9C4B6;
                  border-radius: 4px; margin: 8px 4px; padding: 4px 8px; }
        .tlchipaudio { background: #EFEBE0; border: 1px solid #C9C4B6;
                       border-radius: 4px; margin: 8px 4px; padding: 4px 8px; }
        .tlchiptrans { background: #F4F2EC; border: 1px solid #C9C4B6;
                       border-radius: 4px; margin: 8px 4px; padding: 4px 6px; }
        .tlchiptitle { background: #F4F2EC; border: 1px solid #C9C4B6;
                       border-radius: 4px; margin: 8px 4px; padding: 4px 8px; }
        .tlchipname { font-size: 11px; color: #1A1916; }
        .tlchiptransname { font-size: 10px; color: #1A1916; font-weight: 600; }
        .tlchipcap { background: #F4F2EC; border: 1px solid #C9C4B6;
                     border-radius: 4px; margin: 8px 3px; padding: 4px 8px; }
        /* the music chip sits on the same warm-paper surface + taupe hairline
           as every other lane chip (it was a pale sage green, the one colour in
           this app outside the papertone / ink / signage-red palette); its own
           lane and note glyph are what identify it */
        .tlchipmusic { background: #EFEBE0; border: 1px solid #C9C4B6;
                       border-radius: 4px; margin: 8px 3px; padding: 4px 8px; }
        .lanecell { border-right: 1px solid #D7D2C5; }
        .lanecell.lanesel { background: #EAE3D2; }
        .lanehit:hover .lanecell { background: #F8F7F2; }
        .tlscroll, .tlscroll viewport { background: #F1EEE6; }
        .storyadd { border: 1px dashed #C9C4B6; background: #F8F7F2; }
        .storybadge { font-size: 10px; color: #9A9484; }

        /* ---------- properties (extended controls) ---------- */
        .propcombo { background: #FCFBF8; border: 1px solid #C9C4B6;
                     border-radius: 8px; color: #1A1916; box-shadow: none;
                     padding: 2px 4px; }
        .propcheck { font-size: 12px; color: #3A362E; }
        .propcheck check { min-width: 15px; min-height: 15px; margin-right: 6px; }
        .propmove { padding: 6px 10px; border: 1px solid #C9C4B6;
                    border-radius: 8px; background: #FCFBF8; color: #1A1916;
                    font-size: 12px; box-shadow: none; }
        .propmove:hover { background: #F1EEE6; }

        /* ---------- import dialog ---------- */
        .impcard { background: #F8F7F2; border: 1px solid #C9C4B6;
                   border-radius: 12px; padding: 28px 30px;
                   box-shadow: 3px 3px 0 rgba(26,25,22,0.15); }
        .dlgtitle { font-family: "Newsreader","Liberation Serif","Georgia",serif;
                    font-size: 20px; font-weight: 600; color: #1A1916;
                    margin-bottom: 6px; }
        .impsub { font-size: 12px; color: #6E695E; margin-bottom: 4px; }
        .implist, .implist viewport { background: #FCFBF8;
                    border: 1px solid #D7D2C5; }
        .improw { padding: 9px 12px; border-bottom: 1px solid #D7D2C5; }
        .improw.impsel { background: #EAE3D2; box-shadow: inset 3px 0 0 #C8341E; }
        .imphit:hover .improw { background: #F8F7F2; }
        .impname { font-size: 13px; color: #1A1916; font-weight: 600; }
        .imppath { font-size: 11px; color: #9A9484; }
        .impadded { font-size: 11px; color: #B3AD9E; font-style: italic; }
        .impcount { font-size: 12px; color: #6E695E; }
        .impbtn { padding: 8px 16px; border-radius: 8px; border: 1px solid #C9C4B6;
                  background: #F1EEE6; color: #1A1916; font-size: 13px;
                  box-shadow: none; }
        .impbtn:hover { background: #EAE3D2; }
        .dlgdrop { border: 1px dashed #C9C4B6; border-radius: 12px;
                   background: #FCFBF8; padding: 34px 22px; }
        .dropmain { font-size: 14px; color: #6E695E; }
        .dropsub { font-size: 12px; color: #9A9484; }
        .dlgok { min-width: 96px; min-height: 38px; padding: 0 16px;
                 background: #C8341E; color: #FCFBF8; border-radius: 8px;
                 font-size: 14px; font-weight: 600; box-shadow: none;
                 border: none; }
        .dlgok:hover { background: #B12D19; }
        .dlgok:disabled { background: #B3AD9E; color: #FCFBF8; }

        /* ---------- export progress ---------- */
        .expprog { min-height: 7px; }
        .expprog trough { min-height: 7px; background: #DED4C2; border: none;
                          border-radius: 100px; }
        .expprog progress { min-height: 7px; background: #C8341E; border: none;
                            border-radius: 100px; }
        """
        prov = Gtk.CssProvider()
        try:
            prov.load_from_data(css)
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), prov,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        except Exception:
            # Styling is cosmetic: a CSS parse error or a missing default
            # screen must not stop the app window from constructing.
            pass


if __name__ == "__main__":
    nbapp.run(VideoEditor)
