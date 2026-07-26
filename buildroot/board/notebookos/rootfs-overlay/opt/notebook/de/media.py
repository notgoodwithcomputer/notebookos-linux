#!/usr/bin/env python3
"""
Media Viewer — the Notebook OS image / video viewer (native GTK).

A real toolbar over a large viewing stage with an Info side panel and a
clickable thumbnail filmstrip. Ships empty per the no-seed rule: nothing is
opened at start, so the stage shows the empty-state, the Info fields read "—",
and the filmstrip reads "No items".

Images are decoded with GdkPixbuf (always available) and the toolbar is live:
  • Open           — in-app file chooser rooted at $NB_HOME
  • Zoom in/out    — scale the displayed pixbuf, tracking a real zoom factor,
                     shown as the live percentage in the toolbar
  • Rotate         — pixbuf.rotate_simple(CLOCKWISE)
  • Previous/Next  — walk the sibling image files in the same folder
  • Slideshow      — a GLib.timeout that advances Next, with a start/stop toggle
                     (the button shows a signage-red active state while running)
The live zoom percentage doubles as a Fit-to-window button. Keyboard shortcuts
mirror the toolbar: ← / → (or PageUp/PageDown) step through the folder's images,
+ / − zoom, 0 fits the image to the window, and Ctrl+O opens a file. Manual
navigation (a keypress, a Previous/Next click, or a filmstrip pick) ends a
running slideshow.
The filmstrip beneath the stage holds a thumbnail of every image in the folder;
the current one is selected in signage-red and a click jumps straight to it.
Thumbnails decode lazily off the main loop, so opening a large folder never
blocks. A file path may also be passed as sys.argv[1] — the Finder launches this
module that way when a .png/.jpg is opened — and is displayed on start.

Video is played through GStreamer (a playbin pipeline whose video is embedded via
a gtksink widget; audio goes to ALSA). GStreamer is only *guaranteed* on the
built guest, not on the host running construct_all.py / the selftests, so its
import is GUARDED (GST_OK): if it — or an embeddable video sink — is missing, the
stage shows a neutral note and images continue to work fully. The engine is never
required for the module to import or the window to construct.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib  # noqa: E402

import os
import sys
import time

import nbapp
import nbpicker
import nbicons
from nbi18n import _t  # noqa: E402

# ---- optional video engine (guarded) --------------------------------------
# GStreamer decodes and plays video; it is only *guaranteed* on the built guest.
# The host running construct_all.py / the selftests may lack it, so the import
# is guarded: the module still imports and the window still constructs into its
# neutral state, and GST_OK gates every use of the pipeline below. Images need
# none of this — they decode with GdkPixbuf, which is part of the base stack.
GST_OK = False
try:
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst  # noqa: E402
    GST_OK = True
except (ImportError, ValueError):
    Gst = None

HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))

# Recognised media by extension. Images are decoded and displayed; video is
# played through GStreamer when available (else described in the Info panel).
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".tif",
              ".ico", ".svg", ".heic", ".heif", ".avif")
VIDEO_EXTS = (".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v")
KIND = {
    ".png": "PNG image", ".jpg": "JPEG image", ".jpeg": "JPEG image",
    ".gif": "GIF image", ".bmp": "Bitmap image", ".webp": "WebP image",
    ".tiff": "TIFF image", ".tif": "TIFF image", ".ico": "Icon image",
    ".svg": "SVG image", ".heic": "HEIF image", ".heif": "HEIF image",
    ".avif": "AVIF image",
    ".mp4": "MPEG-4 video", ".webm": "WebM video", ".mov": "QuickTime video",
    ".mkv": "Matroska video", ".avi": "AVI video", ".m4v": "MPEG-4 video",
}
# margin (px) kept clear around the image when fitting it to the stage
FIT_PAD = 24
# zoom factor bounds and the multiplicative step per Zoom in/out click
ZOOM_MIN, ZOOM_MAX, ZOOM_STEP = 0.05, 8.0, 1.25
# a hard cap on the scaled pixel size, so an extreme zoom can never allocate a
# multi-gigabyte pixbuf (scale is trimmed to keep the larger side under this)
MAX_PIX = 8000
# slideshow dwell (ms) before advancing to the next image
SLIDESHOW_MS = 3500
# toolbar icon tone (faint ink, matching the design language)
TOOL_INK = "#9A9484"
# signage-red — reserved for the one active/selected accent in this viewer (the
# current filmstrip thumbnail and the running-slideshow button); never
# decorative, per the design language.
SEL_RED = "#C8341E"


def _decode_to_png(path):
    """Transcode an image GdkPixbuf has no loader for into a temporary PNG, using
    tools that ARE in the image: rsvg-convert for SVG (a vector format ffmpeg
    can't rasterise) and ffmpeg for raster formats — WebP, HEIC/HEIF, AVIF, TIFF
    and anything else libavcodec can decode. Returns the temp PNG path or None."""
    import tempfile
    import shutil
    import subprocess
    ext = os.path.splitext(path)[1].lower()
    fd, out = tempfile.mkstemp(suffix=".png", prefix="nbimg-")
    os.close(fd)
    try:
        if ext == ".svg" and shutil.which("rsvg-convert"):
            cmd = ["rsvg-convert", "-o", out, path]
        elif shutil.which("ffmpeg"):
            cmd = ["ffmpeg", "-y", "-v", "error", "-i", path, "-frames:v", "1", out]
        else:
            os.unlink(out)
            return None
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=25)
        if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0:
            return out
    except Exception:
        pass
    try:
        os.unlink(out)
    except Exception:
        pass
    return None


def _pixbuf_any(path):
    """A GdkPixbuf for `path`. GdkPixbuf's own loaders (PNG/JPEG/GIF/TIFF/BMP/...)
    are tried first; formats it lacks a loader for (WebP, HEIC, SVG, AVIF) fall
    back to an ffmpeg/rsvg transcode. Raises if nothing can decode it."""
    try:
        return GdkPixbuf.Pixbuf.new_from_file(path)
    except Exception:
        tmp = _decode_to_png(path)
        if tmp is None:
            raise
        try:
            return GdkPixbuf.Pixbuf.new_from_file(tmp)
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass
# filmstrip thumbnail decode box (px); the image's aspect ratio is preserved
# within it, and each is decoded lazily off the main loop.
THUMB_W, THUMB_H = 110, 60


def _thumbnail(path):
    """A THUMB_W x THUMB_H thumbnail for `path`, or None if nothing can read it.

    GdkPixbuf's scaling loader covers the formats it has a loader for, but the
    image ships WITHOUT loaders for WebP, HEIC/HEIF, AVIF and SVG — so a folder
    holding those got a strip of blank white cells even though the stage showed
    each picture perfectly (the stage goes through _pixbuf_any, which falls back
    to an ffmpeg/rsvg transcode). Take the same fallback here and scale the
    result down ourselves."""
    try:
        return GdkPixbuf.Pixbuf.new_from_file_at_scale(
            path, THUMB_W, THUMB_H, True)
    except Exception:
        pass
    try:
        pb = _pixbuf_any(path)
    except Exception:
        return None
    w, h = pb.get_width(), pb.get_height()
    if w <= 0 or h <= 0:
        return None
    k = min(THUMB_W / float(w), THUMB_H / float(h), 1.0)
    try:
        return pb.scale_simple(max(1, int(w * k)), max(1, int(h * k)),
                               GdkPixbuf.InterpType.BILINEAR)
    except Exception:
        return None


def human(n):
    """Byte count as a compact human-readable size (matches the Finder)."""
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return ("%d %s" % (n, u)) if u == "B" else ("%.1f %s" % (n, u))
        n /= 1024.0


class MediaViewer(nbapp.AppWindow):
    app_name = "Media Viewer"
    # A pure viewer with no editable text surface, so no Edit menu (Cut/Copy/
    # Paste/Select All would be permanently inert here). File carries Open and
    # Close; View toggles the Info panel and the filmstrip.
    menus = ("File", "View")

    # "Photo" is the file's place in its folder ("12 of 400") — looking through
    # a folder you want to know how far in you are, and the filmstrip only shows
    # the few thumbnails either side.
    INFO_FIELDS = ("Name", "Photo", "Kind", "Dimensions", "File size",
                   "Modified")

    def __init__(self):
        super().__init__()
        self._install_css()

        # -- loaded-media state --
        self._media_path = None        # path of the currently displayed file
        self._orig_pixbuf = None       # working full-res pixbuf (rotated), or None
        self._last_alloc = (0, 0)      # last stage size a fit was computed for
        self._info_vals = {}           # Info field name -> value Gtk.Label
        self._zoom = None              # current absolute display scale, or None
        self._fit_mode = True          # True: auto-fit to stage; False: user zoom
        self._siblings = []            # sibling image paths in the same folder
        self._sib_idx = 0              # index of the current file within siblings
        self._slideshow_id = 0         # GLib source of a running slideshow, or 0
        self._btn = {}                 # toolbar name -> Gtk.Button
        self._btn_img = {}             # toolbar name -> its Gtk.Image (for glyphs)
        self._confirm_layer = None     # the in-window confirm overlay, if open

        # -- filmstrip state --
        self._strip_sig = None         # (paths,) the strip was last built for
        self._strip_btns = {}          # image path -> its thumbnail Gtk.Button
        self._strip_imgs = {}          # image path -> its thumbnail Gtk.Image
        self._thumb_cache = {}         # image path -> decoded thumbnail pixbuf
        self._thumb_queue = []         # image paths still awaiting a lazy decode
        self._thumb_idle_id = 0        # GLib idle source doing the decode, or 0

        # -- video engine state (all inert until a video is opened) --
        self._player = None            # the playbin pipeline, or None
        self._vsink = None             # the embedded gtksink element, or None
        self._vwidget = None           # the sink's GtkWidget, or None
        self._v_playing = False        # transport play/pause toggle
        self._v_poll_id = 0            # GLib source polling video progress, or 0
        self._v_duration_ns = 0        # cached clip duration in ns
        self._v_user_seeking = False   # True while the user drags the seek bar
        self._v_dims_set = False       # Dimensions filled from the video caps yet

        self._toolbar_w = self._toolbar()
        self.content.pack_start(self._toolbar_w, False, False, 0)

        # --- body: viewing stage + info panel ---
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        body.set_hexpand(True)
        body.set_vexpand(True)

        stage = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        stage.get_style_context().add_class("stage")
        stage.set_hexpand(True)
        stage.set_vexpand(True)
        self._stage = stage
        stage.connect("size-allocate", self._on_stage_alloc)

        # empty-state (shown until a file is opened)
        self._empty = self._empty_state()
        stage.pack_start(self._empty, True, False, 0)

        # image surface: the Gtk.Image lives in a scroller so a zoomed-in image
        # pans instead of overflowing the stage; a fit image (never upscaled) is
        # centred by the viewport. Hidden until an image is opened.
        self._img = Gtk.Image()
        self._img.set_halign(Gtk.Align.CENTER)
        self._img.set_valign(Gtk.Align.CENTER)
        self._scroll = Gtk.ScrolledWindow()
        self._scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self._scroll.get_style_context().add_class("imgscroll")
        # Ctrl+wheel zooms the picture, the gesture people already reach for.
        # Deliberately ONLY with Ctrl: making a plain wheel zoom "when the
        # picture fits" and pan otherwise means the same gesture does two
        # different things depending on a state the user cannot see, and having
        # zoomed in once they could not zoom back out the same way.
        self._scroll.add_events(Gdk.EventMask.SCROLL_MASK
                                | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        self._scroll.connect("scroll-event", self._on_scroll)
        self._scroll.add(self._img)          # auto-wraps the Image in a Viewport
        self._scroll.show_all()              # realise viewport + image first,
        self._scroll.set_no_show_all(True)   # so run()'s show_all leaves it
        self._scroll.hide()                  # hidden until an image loads
        stage.pack_start(self._scroll, True, True, 0)

        # video surface (playbin's gtksink widget + transport). Hidden until a
        # video is opened AND the engine is available.
        self._video = self._video_surface()
        stage.pack_start(self._video, True, True, 0)

        # notice surface for missing-engine / decode failures (hidden until used)
        self._notice = self._notice_box()
        stage.pack_start(self._notice, True, False, 0)

        body.pack_start(stage, True, True, 0)
        self._info_w = self._info_panel()
        body.pack_start(self._info_w, False, False, 0)
        self.content.pack_start(body, True, True, 0)

        # --- filmstrip ---
        self._film_w = self._filmstrip()
        self.content.pack_start(self._film_w, False, False, 0)

        self._vfull = False   # video-fullscreen (chrome hidden, video edge-to-edge)

        # settle the toolbar on the empty state (everything inert until a file is
        # opened) and stop the engine cleanly on close.
        self._update_controls()
        self.connect("destroy", self._on_destroy)

        # A path handed in as argv[1] (Finder opens .png/.jpg this way) is shown
        # once the window is realised, so the stage is sized for the fit scale.
        # Deferred to idle so it survives the run() show_all.
        if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
            arg = sys.argv[1]
            GLib.idle_add(lambda: (self._display(arg), False)[1])

    # -- empty / notice surfaces --
    def _empty_state(self):
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        wrap.set_halign(Gtk.Align.CENTER)
        wrap.set_valign(Gtk.Align.CENTER)
        try:
            img = Gtk.Image.new_from_pixbuf(nbicons.pixbuf("media", 56, "#9A9484"))
        except Exception:
            img = Gtk.Image()  # icon render failed -> blank placeholder image
        img.set_halign(Gtk.Align.CENTER)
        wrap.pack_start(img, False, False, 0)
        t1 = Gtk.Label(label=_t("No file open"))
        t1.get_style_context().add_class("stage-title")
        wrap.pack_start(t1, False, False, 0)
        t2 = Gtk.Label(label=_t("Click Open to choose an image or video."))
        t2.get_style_context().add_class("stage-sub")
        wrap.pack_start(t2, False, False, 0)
        return wrap

    def _notice_box(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)
        self._notice_title = Gtk.Label()
        self._notice_title.get_style_context().add_class("stage-title")
        box.pack_start(self._notice_title, False, False, 0)
        self._notice_sub = Gtk.Label()
        self._notice_sub.get_style_context().add_class("stage-sub")
        box.pack_start(self._notice_sub, False, False, 0)
        box.show_all()             # realise children before hiding the parent
        box.set_no_show_all(True)  # so run()'s show_all leaves it hidden
        box.hide()
        return box

    def _video_surface(self):
        """The dark video stage: the playbin sink widget fills it, with a real
        transport (play/pause, timecodes, seek bar) beneath. The sink widget is
        added lazily by _ensure_player(); this only frames the surface."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.get_style_context().add_class("videobox")

        holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        holder.set_hexpand(True)
        holder.set_vexpand(True)
        self._video_holder = holder
        box.pack_start(holder, True, True, 0)

        ctl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        ctl.get_style_context().add_class("vtransport")
        self._v_play = Gtk.Button()
        self._v_play.set_relief(Gtk.ReliefStyle.NONE)
        self._v_play.get_style_context().add_class("toolbtn")
        try:
            self._v_play_img = Gtk.Image.new_from_pixbuf(
                nbicons.pixbuf("play", 16, "#1A1916"))
        except Exception:
            self._v_play_img = Gtk.Image()
        self._v_play.add(self._v_play_img)
        self._v_play.connect("clicked", self._on_video_toggle)
        ctl.pack_start(self._v_play, False, False, 0)

        self._v_elapsed = Gtk.Label(label="0:00")
        self._v_elapsed.get_style_context().add_class("vtimecode")
        ctl.pack_start(self._v_elapsed, False, False, 0)

        self._v_seek = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 1000, 1)
        self._v_seek.set_draw_value(False)
        self._v_seek.set_hexpand(True)
        self._v_seek.connect("button-press-event", self._on_vseek_press)
        self._v_seek.connect("button-release-event", self._on_vseek_release)
        self._v_seek.connect("change-value", self._on_vseek)
        ctl.pack_start(self._v_seek, True, True, 0)

        self._v_total = Gtk.Label(label="0:00")
        self._v_total.get_style_context().add_class("vtimecode")
        ctl.pack_start(self._v_total, False, False, 0)

        # fullscreen toggle — fills the screen with just the video (F, or Esc to
        # leave). A plain, glyph-free label keeps it tofu-proof on real hardware.
        self._v_full_btn = Gtk.Button(label=_t("Fullscreen"))
        self._v_full_btn.set_relief(Gtk.ReliefStyle.NONE)
        self._v_full_btn.get_style_context().add_class("toolbtn")
        self._v_full_btn.set_tooltip_text(_t("Fullscreen video (F)"))
        self._v_full_btn.connect("clicked", lambda *_: self._toggle_video_fullscreen())
        ctl.pack_start(self._v_full_btn, False, False, 0)
        box.pack_start(ctl, False, False, 0)

        box.show_all()             # realise the transport children first
        box.set_no_show_all(True)  # so run()'s show_all leaves it hidden
        box.hide()
        return box

    def _show_surface(self, which):
        """Reveal exactly one stage surface: 'empty', 'image', 'video' or
        'notice'; the others are hidden."""
        self._empty.set_visible(which == "empty")
        self._scroll.set_visible(which == "image")
        self._video.set_visible(which == "video")
        self._notice.set_visible(which == "notice")

    # -- toolbar --
    def _toolbar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        bar.get_style_context().add_class("toolbar")
        bar.set_size_request(-1, 54)

        openb = Gtk.Button()
        openb.set_relief(Gtk.ReliefStyle.NONE)
        openb.get_style_context().add_class("openbtn")
        ob = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        try:
            ob.pack_start(Gtk.Image.new_from_pixbuf(
                nbicons.pixbuf("folder", 15, "#1A1916")), False, False, 0)
        except Exception:
            pass  # icon render failed -> omit the glyph, keep the Open button
        ob.pack_start(Gtk.Label(label=_t("Open")), False, False, 0)
        openb.add(ob)
        openb.set_tooltip_text(_t("Open a file  (Ctrl+O)"))
        openb.connect("clicked", self._on_open)
        bar.pack_start(openb, False, False, 0)

        bar.pack_start(self._vsep(), False, False, 4)
        bar.pack_start(self._tool("prev", "Previous image  (←)", self._on_prev),
                       False, False, 0)
        bar.pack_start(self._tool("next", "Next image  (→)", self._on_next),
                       False, False, 0)
        bar.pack_start(self._vsep(), False, False, 4)
        bar.pack_start(self._tool("zoomout", "Zoom out  (-)", self._on_zoom_out),
                       False, False, 0)
        # The live zoom percentage is also the Fit-to-window control: a click (or
        # the 0 key) refits a manually-zoomed image to the stage.
        fitb = Gtk.Button()
        fitb.set_relief(Gtk.ReliefStyle.NONE)
        fitb.get_style_context().add_class("toolbtn")
        fitb.get_style_context().add_class("fitbtn")
        fitb.set_tooltip_text(_t("Fit to window  (0)"))
        pct = Gtk.Label(label="—")
        pct.get_style_context().add_class("zoompct")
        pct.set_size_request(44, -1)
        self._zoom_lbl = pct
        fitb.add(pct)
        fitb.connect("clicked", self._on_fit)
        self._fit_btn = fitb
        bar.pack_start(fitb, False, False, 0)
        bar.pack_start(self._tool("zoomin", "Zoom in  (+)", self._on_zoom_in),
                       False, False, 0)
        bar.pack_start(self._tool("rotate", "Rotate right", self._on_rotate),
                       False, False, 0)
        # Getting rid of a bad shot is the other half of looking through a
        # folder, and there was no way to do it here at all. It goes to the same
        # Trash the Finder uses, so it is a move, not a deletion.
        bar.pack_start(self._vsep(), False, False, 4)
        bar.pack_start(self._tool("trash", "Move to Trash  (Delete)",
                                  self._on_trash), False, False, 0)

        bar.pack_start(Gtk.Box(), True, True, 0)  # flex spacer
        bar.pack_end(self._tool("play", "Start slideshow", self._on_slideshow),
                     False, False, 0)
        return bar

    def _tool(self, name, tip, cb):
        """A flat pictographic toolbar button, wired to `cb`. Sensitivity is
        governed dynamically by _update_controls() (inert until there is media
        to act on); the button itself is fully live."""
        b = Gtk.Button()
        b.set_relief(Gtk.ReliefStyle.NONE)
        b.get_style_context().add_class("toolbtn")
        b.set_tooltip_text(tip)
        img = None
        try:
            img = Gtk.Image.new_from_pixbuf(nbicons.pixbuf(name, 16, TOOL_INK))
            b.add(img)
        except Exception:
            pass  # icon render failed -> leave the toolbar button glyphless
        b.connect("clicked", cb)
        self._btn[name] = b
        self._btn_img[name] = img
        return b

    def _vsep(self):
        s = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        s.get_style_context().add_class("vsep")
        return s

    def _set_sensitive(self, name, on):
        b = self._btn.get(name)
        if b is not None:
            b.set_sensitive(bool(on))

    def _update_controls(self):
        """Enable each toolbar control only when it has something to act on: the
        zoom/rotate tools once an image is loaded, and Previous/Next/Slideshow
        once the folder holds more than one image."""
        has_img = self._orig_pixbuf is not None
        multi = len(self._siblings) > 1
        self._set_sensitive("zoomin", has_img)
        self._set_sensitive("zoomout", has_img)
        self._set_sensitive("rotate", has_img)
        self._set_sensitive("prev", multi)
        self._set_sensitive("next", multi)
        self._set_sensitive("play", multi)
        self._set_sensitive("trash", bool(self._media_path))
        if getattr(self, "_fit_btn", None) is not None:
            self._fit_btn.set_sensitive(has_img)

    # -- info panel --
    def _info_panel(self):
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.get_style_context().add_class("infopanel")
        panel.set_size_request(320, -1)

        head = Gtk.Label(label=_t("INFO"), xalign=0)
        head.get_style_context().add_class("info-head")
        panel.pack_start(head, False, False, 0)

        for name in self.INFO_FIELDS:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            row.get_style_context().add_class("info-row")
            lbl = Gtk.Label(label=name, xalign=0)
            lbl.get_style_context().add_class("info-key")
            val = Gtk.Label(label="—", xalign=1)
            val.set_ellipsize(3)  # PANGO_ELLIPSIZE_END: keep long names in-panel
            val.set_max_width_chars(22)
            val.get_style_context().add_class("info-val")
            row.pack_start(lbl, False, False, 0)
            row.pack_end(val, False, False, 0)
            panel.pack_start(row, False, False, 0)
            self._info_vals[name] = val
        return panel

    def _set_info(self, mapping):
        """Write the given {field: text}; any field omitted is reset to '—'."""
        for name in self.INFO_FIELDS:
            val = self._info_vals.get(name)
            if val is not None:
                val.set_text(mapping.get(name, "—"))

    def _fill_info(self, path, pixbuf):
        try:
            st = os.stat(path)
        except OSError:
            st = None
        ext = os.path.splitext(path)[1].lower()
        info = {
            "Name": os.path.basename(path),
            "Kind": KIND.get(ext, "File"),
        }
        if len(self._siblings) > 1:
            info["Photo"] = _t("%d of %d") % (self._sib_idx + 1,
                                              len(self._siblings))
        if pixbuf is not None:
            info["Dimensions"] = "%d × %d px" % (
                pixbuf.get_width(), pixbuf.get_height())
        if st is not None:
            info["File size"] = human(st.st_size)
            info["Modified"] = time.strftime(
                "%Y-%m-%d %H:%M", time.localtime(st.st_mtime))
        self._set_info(info)

    def _set_zoom(self, scale):
        self._zoom_lbl.set_text("—" if scale is None
                                else "%d%%" % int(round(scale * 100)))

    # -- filmstrip (thumbnails of the folder's images) --
    def _filmstrip(self):
        """The thumbnail strip beneath the stage: 'No media' until a folder of
        images is open, then one clickable thumbnail per sibling image with the
        current one selected in signage-red. Thumbnails decode lazily."""
        strip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        strip.get_style_context().add_class("filmstrip")
        strip.set_size_request(-1, 96)

        empty = Gtk.Label(label=_t("No media"))
        empty.set_hexpand(True)
        empty.set_halign(Gtk.Align.CENTER)
        empty.set_valign(Gtk.Align.CENTER)
        empty.get_style_context().add_class("strip-empty")
        strip.pack_start(empty, True, True, 0)
        self._strip_empty = empty

        scroll = Gtk.ScrolledWindow()
        # horizontal scroll only; the strip never grows a vertical scrollbar
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroll.get_style_context().add_class("stripscroll")
        scroll.set_hexpand(True)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        row.get_style_context().add_class("filmrow")
        scroll.add(row)               # auto-wraps the row in a viewport
        scroll.show_all()             # realise viewport + row first,
        scroll.set_no_show_all(True)  # so run()'s show_all leaves it
        scroll.hide()                 # hidden until a folder of images opens
        strip.pack_start(scroll, True, True, 0)
        self._strip_scroll = scroll
        self._strip_row = row
        return strip

    def _strip_entries(self):
        """The image files the filmstrip represents: the image siblings in the
        current folder (empty for a video or when nothing is open)."""
        return [p for p in self._siblings
                if os.path.splitext(p)[1].lower() in IMAGE_EXTS]

    def _rebuild_strip(self):
        """Rebuild the filmstrip for the current folder, but only when the set of
        image siblings actually changed — walking Previous/Next within a folder
        just re-highlights, never re-decodes."""
        entries = self._strip_entries()
        sig = tuple(entries)
        if sig == self._strip_sig:
            self._highlight_strip()
            self._scroll_strip_to(self._media_path)
            return
        self._strip_sig = sig
        self._cancel_thumbs()
        for child in self._strip_row.get_children():
            self._strip_row.remove(child)
        self._strip_btns = {}
        self._strip_imgs = {}
        # bound memory: keep only thumbnails still on the strip
        keep = set(entries)
        self._thumb_cache = {p: pb for p, pb in self._thumb_cache.items()
                             if p in keep}
        if not entries:
            # "No media" is only true before anything is opened; with a video
            # (or a lone image) on screen it contradicts what the user is
            # looking at, so say what the strip is actually missing.
            self._strip_empty.set_text(
                _t("No other images in this folder") if self._media_path
                else "No media")
            self._strip_scroll.hide()
            self._strip_empty.show()
            return
        self._strip_empty.hide()
        self._strip_scroll.show()
        pending = []
        for p in entries:
            self._strip_row.pack_start(
                self._thumb_cell(p, pending), False, False, 0)
        self._strip_row.show_all()
        self._highlight_strip()
        self._scroll_strip_to(self._media_path)
        if pending:
            self._thumb_queue = pending
            if self._thumb_idle_id == 0:
                self._thumb_idle_id = GLib.idle_add(self._thumb_tick)

    def _thumb_cell(self, path, pending):
        """One filmstrip cell: a flat paper button around a thumbnail Gtk.Image,
        wired to display `path`. If the thumbnail isn't cached yet the path is
        queued for a lazy decode and the cell fills in shortly."""
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.get_style_context().add_class("filmcell")
        btn.set_tooltip_text(os.path.basename(path))
        img = Gtk.Image()
        img.set_halign(Gtk.Align.CENTER)
        img.set_valign(Gtk.Align.CENTER)
        cached = self._thumb_cache.get(path)
        if cached is not None:
            img.set_from_pixbuf(cached)
        else:
            pending.append(path)
        btn.add(img)
        btn.connect("clicked", self._on_strip_click, path)
        self._strip_btns[path] = btn
        self._strip_imgs[path] = img
        return btn

    def _thumb_tick(self):
        """Decode one queued thumbnail per idle pass, so a large folder fills the
        strip without ever blocking the GTK main loop."""
        if not self._thumb_queue:
            self._thumb_idle_id = 0
            return False
        path = self._thumb_queue.pop(0)
        img = self._strip_imgs.get(path)
        if img is not None:
            pb = self._thumb_cache.get(path)
            if pb is None:
                pb = _thumbnail(path)
                if pb is not None:
                    self._thumb_cache[path] = pb
            try:
                if pb is not None:
                    img.set_from_pixbuf(pb)
                else:
                    # nothing could read the file — a faint glyph reads as
                    # "no preview" instead of an empty, broken-looking cell
                    img.set_from_pixbuf(nbicons.pixbuf("media", 22, "#C9C4B6"))
            except Exception:
                pass
        if self._thumb_queue:
            return True
        self._thumb_idle_id = 0
        return False

    def _cancel_thumbs(self):
        if self._thumb_idle_id:
            try:
                GLib.source_remove(self._thumb_idle_id)
            except Exception:
                pass
            self._thumb_idle_id = 0
        self._thumb_queue = []

    def _highlight_strip(self):
        """Mark the current file's thumbnail selected (signage-red), clearing the
        selection on every other cell."""
        cur = self._media_path
        for path, btn in self._strip_btns.items():
            ctx = btn.get_style_context()
            if path == cur:
                ctx.add_class("filmsel")
            else:
                ctx.remove_class("filmsel")

    def _scroll_strip_to(self, path):
        """Bring the selected thumbnail into view (deferred to idle so the cell
        has been allocated a position first)."""
        btn = self._strip_btns.get(path)
        if btn is None:
            return

        def _do():
            try:
                adj = self._strip_scroll.get_hadjustment()
                if adj is not None:
                    a = btn.get_allocation()
                    page = adj.get_page_size()
                    target = a.x + a.width / 2.0 - page / 2.0
                    hi = max(adj.get_lower(), adj.get_upper() - page)
                    adj.set_value(min(max(adj.get_lower(), target), hi))
            except Exception:
                pass
            return False
        GLib.idle_add(_do)

    def _on_strip_click(self, _b, path):
        if path == self._media_path or not os.path.isfile(path):
            return
        self._stop_slideshow()   # a manual pick ends any running slideshow
        self._display(path)

    # -- open / display --
    def _on_open(self, _b=None):
        path = self._choose_file()
        if path and os.path.isfile(path):
            self._stop_slideshow()   # a fresh Open ends any running slideshow
            self._display(path)

    def _choose_file(self):
        """Finder-style in-app picker rooted at $NB_HOME; a path or None."""
        try:
            os.makedirs(HOME, exist_ok=True)
        except OSError:
            pass
        base = (os.path.dirname(self._media_path)
                if self._media_path else HOME)
        start = base if os.path.isdir(base) else HOME
        pats = tuple("*" + e for e in (list(IMAGE_EXTS) + list(VIDEO_EXTS)))
        return nbpicker.open_file(self, title="Open Media",
                                  start_dir=start, patterns=pats)

    def _scan_siblings(self, path):
        """Populate self._siblings with the image files in the same folder as
        `path` (name-sorted, full paths) and set _sib_idx to `path`'s position.
        A video (or a lone/orphan file) becomes a standalone list of one."""
        folder = os.path.dirname(path) or "."
        base = os.path.basename(path)
        try:
            names = sorted(os.listdir(folder), key=str.lower)
        except OSError:
            names = []
        imgs = [n for n in names
                if os.path.splitext(n)[1].lower() in IMAGE_EXTS
                and os.path.isfile(os.path.join(folder, n))]
        if base not in imgs:
            # current file is not an image sibling (e.g. a video) -> standalone
            self._siblings = [path]
            self._sib_idx = 0
            return
        self._siblings = [os.path.join(folder, n) for n in imgs]
        self._sib_idx = imgs.index(base)

    def _display(self, path, rescan=True):
        """Load and show `path`: play video through GStreamer, decode images with
        GdkPixbuf, and report anything unreadable with a neutral notice."""
        self._media_path = path
        ext = os.path.splitext(path)[1].lower()
        if rescan:
            self._scan_siblings(path)

        if ext in VIDEO_EXTS:
            # video is standalone: reset image state and hand off to the engine
            self._stop_slideshow()
            self._orig_pixbuf = None
            self._zoom = None
            self._set_zoom(None)
            self._show_video(path)
            self._fill_info(path, None)
            self._update_controls()
            self._rebuild_strip()
            return

        # image path — never needs the video engine
        self._stop_video()
        try:
            pixbuf = _pixbuf_any(path)
        except Exception:
            self._orig_pixbuf = None
            self._zoom = None
            self._stop_slideshow()
            # same voice as the video failure below: what happened, then a way on
            self._show_notice(
                "This file cannot be opened",
                "The file may be damaged, or saved in a format Notebook OS "
                "does not read. Try another file.")
            self._fill_info(path, None)
            self._set_zoom(None)
            self._update_controls()
            self._rebuild_strip()
            return

        self._orig_pixbuf = pixbuf
        self._fit_mode = True           # a freshly opened image fits the stage
        self._last_alloc = (0, 0)       # force a re-fit for the new image
        self._show_surface("image")
        self._render_image()
        self._fill_info(path, pixbuf)
        self._update_controls()
        self._rebuild_strip()

    def _show_notice(self, title, sub):
        self._notice_title.set_text(title)
        self._notice_sub.set_text(sub)
        self._show_surface("notice")

    # -- move the shown file to the Trash --
    def _on_trash(self, _b=None):
        """Move the file on screen to the Trash, after confirming. It is the
        same $NB_HOME/.Trash the Finder uses, so nothing is destroyed: the
        Finder can put it back."""
        path = self._media_path
        if not (path and os.path.isfile(path)):
            return
        self._stop_slideshow()      # taking manual control ends a slideshow
        self._confirm(
            _t("Move to Trash"),
            _t("“%s” moves to the Trash. You can put it back from the Finder.")
            % os.path.basename(path),
            _t("Move to Trash"), lambda: self._do_trash(path))

    def _do_trash(self, path):
        import shutil
        trash = os.path.join(HOME, ".Trash")
        base = os.path.basename(path)
        try:
            os.makedirs(trash, exist_ok=True)
            dst = os.path.join(trash, base)
            n = 1
            while os.path.exists(dst):
                dst = os.path.join(trash, "%s (%d)" % (base, n))
                n += 1
            try:
                os.rename(path, dst)
            except OSError:
                # a file on another disk (a memory card, a USB stick) cannot be
                # renamed across the filesystem boundary — copy it over instead
                shutil.move(path, dst)
        except (OSError, shutil.Error):
            self._show_notice(
                _t("This file could not be moved to the Trash"),
                _t("The disk may be full or write-protected."))
            return
        # carry on where the user was: show the next picture in the folder (or
        # the one before it, if the trashed shot was the last)
        remaining = [p for p in self._siblings if p != path]
        if not remaining:
            self._media_path = None
            self._orig_pixbuf = None
            self._zoom = None
            self._siblings = []
            self._sib_idx = 0
            self._set_zoom(None)
            self._set_info({})
            self._show_surface("empty")
            self._update_controls()
            self._rebuild_strip()
            return
        self._siblings = remaining
        self._sib_idx = min(self._sib_idx, len(remaining) - 1)
        self._thumb_cache.pop(path, None)
        self._display(remaining[self._sib_idx], rescan=False)

    # -- in-window confirmation (reliable on the no-compositor stack) --
    def _confirm(self, title, message, ok_label, on_yes):
        """House-style destructive confirm: a card centred over a scrim inside
        the app's own overlay — no separate popup window."""
        self._close_confirm()
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        card.get_style_context().add_class("vdlg")
        card.set_size_request(360, -1)
        t = Gtk.Label(label=title, xalign=0)
        t.get_style_context().add_class("vdlg-title")
        card.pack_start(t, False, False, 0)
        m = Gtk.Label(label=message, xalign=0)
        m.get_style_context().add_class("vdlg-body")
        m.set_line_wrap(True)
        m.set_max_width_chars(40)
        card.pack_start(m, False, False, 0)
        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btns.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label=_t("Cancel"))
        cancel.get_style_context().add_class("vdlg-btn")
        cancel.connect("clicked", lambda *_: self._close_confirm())
        ok = Gtk.Button(label=ok_label)
        ok.get_style_context().add_class("vdlg-btn")
        ok.get_style_context().add_class("vdlg-primary")
        ok.connect("clicked", lambda *_: (self._close_confirm(), on_yes()))
        btns.pack_start(cancel, False, False, 0)
        btns.pack_start(ok, False, False, 0)
        card.pack_start(btns, False, False, 0)

        alloc = self.get_allocation()
        _sw, _sh = nbapp.screen_size()
        W = alloc.width if alloc.width > 1 else _sw
        H = alloc.height if alloc.height > 1 else _sh
        layer = Gtk.Fixed()
        scrim = Gtk.EventBox()
        scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        scrim.set_size_request(W, H)
        scrim.connect("button-press-event",
                      lambda *a: (self._close_confirm(), True)[1])
        layer.put(scrim, 0, 0)
        holder = Gtk.EventBox()     # own GdkWindow so the card blits on top
        holder.add(card)
        layer.put(holder, 0, 0)
        self._overlay.add_overlay(layer)
        layer.show_all()
        _min, nat = holder.get_preferred_size()
        cw = nat.width if nat.width > 1 else 360
        ch = nat.height if nat.height > 1 else 160
        layer.move(holder, max((W - cw) // 2, 0), max((H - ch) // 2, 0))
        self._confirm_layer = layer
        # land focus on the SAFE choice, so a reflexive Enter cancels
        try:
            cancel.grab_focus()
        except Exception:
            pass

    def _close_confirm(self, *_):
        layer = getattr(self, "_confirm_layer", None)
        if layer is not None:
            try:
                self._overlay.remove(layer)
            except Exception:
                pass
            self._confirm_layer = None
            return True
        return False

    # -- image scaling / zoom / rotate --
    def _fit_scale(self):
        """The scale that fits the working pixbuf inside the stage (never
        upscaled). 1.0 when there is nothing to measure."""
        pb = self._orig_pixbuf
        if pb is None:
            return 1.0
        alloc = self._stage.get_allocation()
        aw = max(alloc.width - 2 * FIT_PAD, 1)
        ah = max(alloc.height - 2 * FIT_PAD, 1)
        ow, oh = pb.get_width(), pb.get_height()
        if ow <= 0 or oh <= 0:
            return 1.0
        return min(aw / ow, ah / oh, 1.0)

    def _current_scale(self):
        """The absolute display scale to render at now: the fit-to-stage scale
        in fit mode, else the user's chosen zoom factor."""
        if self._fit_mode or self._zoom is None:
            return self._fit_scale()
        return self._zoom

    def _render_image(self):
        """Scale the working pixbuf by the current scale, show it, and update the
        real zoom percentage. Safe to call whenever an image is loaded."""
        pb = self._orig_pixbuf
        if pb is None:
            return
        scale = max(ZOOM_MIN, min(ZOOM_MAX, self._current_scale()))
        ow, oh = pb.get_width(), pb.get_height()
        if ow <= 0 or oh <= 0:
            return
        nw = max(1, int(round(ow * scale)))
        nh = max(1, int(round(oh * scale)))
        big = max(nw, nh)
        if big > MAX_PIX:               # keep an extreme zoom from OOM-ing
            k = MAX_PIX / float(big)
            nw = max(1, int(nw * k))
            nh = max(1, int(nh * k))
            scale *= k
        self._zoom = scale
        try:
            scaled = pb.scale_simple(nw, nh, GdkPixbuf.InterpType.BILINEAR)
            self._img.set_from_pixbuf(scaled)
        except Exception:
            pass
        self._set_zoom(scale)

    def _zoom_by(self, factor):
        if self._orig_pixbuf is None:
            return
        base = self._zoom if self._zoom else self._fit_scale()
        self._fit_mode = False          # a manual zoom leaves fit mode
        self._zoom = max(ZOOM_MIN, min(ZOOM_MAX, base * factor))
        self._render_image()

    def _on_zoom_in(self, _b=None):
        self._zoom_by(ZOOM_STEP)

    def _on_zoom_out(self, _b=None):
        self._zoom_by(1.0 / ZOOM_STEP)

    def _fit_to_window(self):
        """Return a manually-zoomed image to the auto-fit scale (so it fills the
        stage again and tracks resizes). A no-op when nothing is loaded."""
        if self._orig_pixbuf is None:
            return
        self._fit_mode = True
        self._last_alloc = (0, 0)    # force a re-fit for the current stage size
        self._render_image()

    def _on_fit(self, _b=None):
        self._fit_to_window()

    def _on_rotate(self, _b=None):
        if self._orig_pixbuf is None:
            return
        try:
            self._orig_pixbuf = self._orig_pixbuf.rotate_simple(
                GdkPixbuf.PixbufRotation.CLOCKWISE)
        except Exception:
            return
        # keep the current mode/zoom across the rotate; fit mode refits to the
        # swapped dimensions, manual mode keeps the user's factor.
        self._render_image()
        self._fill_info(self._media_path, self._orig_pixbuf)  # dims swapped

    def _on_scroll(self, _w, ev):
        if self._orig_pixbuf is None:
            return False
        if not (ev.state & Gdk.ModifierType.CONTROL_MASK):
            return False              # let the scroller pan, as it always did
        d = ev.direction
        if d == Gdk.ScrollDirection.UP:
            self._on_zoom_in()
        elif d == Gdk.ScrollDirection.DOWN:
            self._on_zoom_out()
        elif d == Gdk.ScrollDirection.SMOOTH:
            ok, _dx, dy = ev.get_scroll_deltas()
            if not ok or not dy:
                return False
            (self._on_zoom_out if dy > 0 else self._on_zoom_in)()
        else:
            return False
        return True

    def _on_stage_alloc(self, _w, _alloc):
        # Re-fit on resize, but only in fit mode; the _last_alloc guard makes
        # redundant allocations (including the one our own set_from_pixbuf
        # triggers) a no-op, and a manual zoom is left untouched.
        if self._orig_pixbuf is None or not self._fit_mode:
            return
        alloc = self._stage.get_allocation()
        aw = max(alloc.width - 2 * FIT_PAD, 1)
        ah = max(alloc.height - 2 * FIT_PAD, 1)
        if (aw, ah) == self._last_alloc:
            return
        self._last_alloc = (aw, ah)
        self._render_image()

    # -- previous / next / slideshow --
    def _step(self, delta):
        sibs = self._siblings
        if not sibs or len(sibs) <= 1:
            return
        self._sib_idx = (self._sib_idx + delta) % len(sibs)
        # reuse the sibling list we already scanned (no re-listdir per step)
        self._display(sibs[self._sib_idx], rescan=False)

    def _on_prev(self, _b=None):
        self._stop_slideshow()   # taking manual control ends a running slideshow
        self._step(-1)

    def _on_next(self, _b=None):
        self._stop_slideshow()   # taking manual control ends a running slideshow
        self._step(1)

    def _on_slideshow(self, _b=None):
        if self._slideshow_id:
            self._stop_slideshow()
        else:
            self._start_slideshow()

    def _start_slideshow(self):
        if len(self._siblings) <= 1 or self._slideshow_id:
            return
        self._slideshow_id = GLib.timeout_add(SLIDESHOW_MS, self._slideshow_tick)
        self._set_slide_active(True)

    def _stop_slideshow(self):
        if self._slideshow_id:
            try:
                GLib.source_remove(self._slideshow_id)
            except Exception:
                pass
            self._slideshow_id = 0
        self._set_slide_active(False)

    def _slideshow_tick(self):
        self._step(1)
        return True   # keep advancing until toggled off

    def _set_slide_active(self, on):
        """Reflect the slideshow's running state on its toolbar button: a
        signage-red stop-square on a faint red cell while running, the neutral
        play glyph when idle. Red is the one active/selected accent here."""
        btn = self._btn.get("play")
        if btn is not None:
            ctx = btn.get_style_context()
            if on:
                ctx.add_class("active")
            else:
                ctx.remove_class("active")
            btn.set_tooltip_text(_t("Stop slideshow") if on else "Start slideshow")
        img = self._btn_img.get("play")
        if img is not None:
            try:
                img.set_from_pixbuf(nbicons.pixbuf(
                    "stopsq" if on else "play", 16,
                    SEL_RED if on else TOOL_INK))
            except Exception:
                pass

    # -- video engine (GStreamer playbin + embedded gtksink) --
    def _ensure_player(self):
        """Create the playbin pipeline once, with its video embedded in a
        gtksink (or gtkglsink) widget packed into the video surface. Returns
        True only when a real, embeddable pipeline exists; False (missing Gst or
        sink) leaves the caller to show a neutral note."""
        if not GST_OK:
            return False
        if self._player is not None:
            return True
        try:
            Gst.init(None)
            player = Gst.ElementFactory.make("playbin", "player")
            if player is None:
                return False
            sink = (Gst.ElementFactory.make("gtksink", "vsink")
                    or Gst.ElementFactory.make("gtkglsink", "vsink"))
            if sink is None:
                return False        # no widget-embeddable sink in this build
            widget = sink.get_property("widget")
            if widget is None:
                return False
            player.set_property("video-sink", sink)
            # Multithread the software decode. libav (avdec_*) uses a single
            # decode thread by default; on this GPU-less stack that makes HD
            # video crawl. element-setup fires as playbin auto-plugs each element
            # (before it goes PLAYING), so we raise max-threads to 0 (= use every
            # core) on the decoders — the single biggest win for smooth playback.
            try:
                player.connect("element-setup", self._on_element_setup)
            except Exception:
                pass
            widget.set_hexpand(True)
            widget.set_vexpand(True)
            widget.show()
            # Click the picture to play/pause and double-click for fullscreen,
            # the two gestures every video player has. The GStreamer sink
            # widget does not deliver button events itself, so it rides inside
            # an EventBox that does.
            tap = Gtk.EventBox()
            tap.set_visible_window(False)
            tap.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            tap.connect("button-press-event", self._on_video_click)
            tap.add(widget)
            tap.show()
            self._video_holder.pack_start(tap, True, True, 0)
            bus = player.get_bus()
            bus.add_signal_watch()
            bus.connect("message::eos", self._on_video_eos)
            bus.connect("message::error", self._on_video_error)
            self._player = player
            self._vsink = sink
            self._vwidget = widget
            return True
        except Exception:
            self._player = None
            return False

    def _on_element_setup(self, _playbin, element):
        """Give software video decoders every CPU core. Guarded: only libav
        decoders have max-threads, and setting it is best-effort."""
        try:
            fac = element.get_factory()
            name = fac.get_name() if fac is not None else ""
        except Exception:
            name = ""
        if name.startswith("avdec_"):
            try:
                element.set_property("max-threads", 0)
            except Exception:
                pass

    def _show_video(self, path):
        if not self._ensure_player():
            # engine (or embeddable sink) missing — images still work fully
            # names no internal tool: say what cannot happen and what still can
            self._show_notice(
                "Video cannot be played",
                "This copy of Notebook OS is missing the part that plays "
                "video. Photos and pictures still open normally.")
            return
        try:
            self._player.set_state(Gst.State.NULL)
            self._player.set_property("uri", Gst.filename_to_uri(path))
            self._v_duration_ns = 0
            self._v_dims_set = False
            self._v_elapsed.set_text("0:00")
            self._v_total.set_text("0:00")
            self._set_seek(0)
            self._show_surface("video")
            self._player.set_state(Gst.State.PLAYING)
            self._v_playing = True
            self._set_video_glyph("pause")
            self._start_video_poll()
        except Exception:
            self._stop_video()
            self._show_notice(
                "This video cannot be played",
                "The file may be damaged, or saved in a format Notebook OS "
                "does not read. Try another video.")

    def _stop_video(self):
        """Halt the pipeline and its progress poll (surface switching is the
        caller's job)."""
        if self._v_poll_id:
            try:
                GLib.source_remove(self._v_poll_id)
            except Exception:
                pass
            self._v_poll_id = 0
        self._v_playing = False
        if self._player is not None:
            try:
                self._player.set_state(Gst.State.NULL)
            except Exception:
                pass

    def _on_video_toggle(self, *_):
        if self._player is None:
            return
        try:
            if self._v_playing:
                self._player.set_state(Gst.State.PAUSED)
                self._v_playing = False
                self._set_video_glyph("play")
            else:
                self._player.set_state(Gst.State.PLAYING)
                self._v_playing = True
                self._set_video_glyph("pause")
                self._start_video_poll()
        except Exception:
            pass

    def _set_video_glyph(self, name):
        # 'pause' marks a playing clip (the button pauses it); 'play' marks a
        # paused clip (the button resumes it). Keep the tooltip in step.
        try:
            self._v_play.set_tooltip_text(
                _t("Pause") if name == "pause" else "Play")
        except Exception:
            pass
        try:
            self._v_play_img.set_from_pixbuf(nbicons.pixbuf(name, 16, "#1A1916"))
        except Exception:
            pass

    def _on_video_eos(self, *_):
        # a clip finished — rewind to the start and pause, so the last frame is
        # not left frozen mid-transport.
        try:
            self._player.seek_simple(
                Gst.Format.TIME,
                Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, 0)
            self._player.set_state(Gst.State.PAUSED)
            self._v_playing = False
            self._set_video_glyph("play")
            self._set_seek(0)
        except Exception:
            pass

    def _on_video_error(self, _bus, _msg):
        # a decode/pipeline error — stop cleanly and show a neutral note rather
        # than wedge on the file.
        self._stop_video()
        self._show_notice(
            "This video cannot be played",
            "The file may be damaged, or saved in a format Notebook OS "
            "does not read. Try another video.")

    def _start_video_poll(self):
        if self._v_poll_id == 0:
            self._v_poll_id = GLib.timeout_add(300, self._on_video_poll)

    def _on_video_poll(self):
        if self._player is None:
            self._v_poll_id = 0
            return False
        try:
            self._update_video_progress()
        except Exception:
            pass
        return True

    def _update_video_progress(self):
        if self._v_user_seeking:
            return
        ok_d, dur = self._player.query_duration(Gst.Format.TIME)
        if ok_d and dur and dur > 0:
            self._v_duration_ns = dur
            self._v_total.set_text(self._fmt_ns(dur))
        ok_p, pos = self._player.query_position(Gst.Format.TIME)
        if ok_p and pos is not None and pos >= 0:
            self._v_elapsed.set_text(self._fmt_ns(pos))
            if self._v_duration_ns > 0:
                frac = max(0.0, min(1.0, pos / float(self._v_duration_ns)))
                self._set_seek(frac * 1000.0)
        if not self._v_dims_set:
            self._grab_video_dims()

    def _grab_video_dims(self):
        """Fill the Info panel's Dimensions once the sink has negotiated caps."""
        try:
            pad = self._vsink.get_static_pad("sink") if self._vsink else None
            caps = pad.get_current_caps() if pad is not None else None
            if caps is None or caps.get_size() == 0:
                return
            st = caps.get_structure(0)
            okw, w = st.get_int("width")
            okh, h = st.get_int("height")
            if okw and okh and w > 0 and h > 0:
                val = self._info_vals.get("Dimensions")
                if val is not None:
                    val.set_text("%d × %d px" % (w, h))
                self._v_dims_set = True
        except Exception:
            pass

    def _set_seek(self, v):
        try:
            self._v_seek.set_value(max(0.0, min(1000.0, v)))
        except Exception:
            pass

    def _on_vseek_press(self, *_):
        self._v_user_seeking = True
        return False

    def _on_vseek_release(self, *_):
        self._v_user_seeking = False
        return False

    def _on_vseek(self, _scale, _scroll, value):
        try:
            if self._player is not None and self._v_duration_ns > 0:
                v = max(0.0, min(1000.0, value))
                ns = int(self._v_duration_ns * (v / 1000.0))
                self._player.seek_simple(
                    Gst.Format.TIME,
                    Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, ns)
        except Exception:
            pass
        return False

    @staticmethod
    def _fmt_ns(ns):
        """A GStreamer nanosecond count as an m:ss timecode."""
        try:
            secs = int(ns // 1000000000)
        except Exception:
            return "0:00"
        if secs < 0:
            secs = 0
        return "%d:%02d" % (secs // 60, secs % 60)

    def _on_destroy(self, *_):
        self._stop_slideshow()
        self._stop_video()
        self._cancel_thumbs()

    # -- keyboard --
    def _on_key(self, w, ev):
        """Viewer shortcuts, layered over the base. Ctrl+O opens a file; with an
        image loaded, +/- zoom and 0 fits to the window; with more than one image
        in the folder, ← / → (or PageUp/PageDown) step through them. Everything
        else — including Esc, which the base uses to dismiss overlays / close —
        falls through to super()._on_key. Keys are only claimed when no dropdown
        menu or About card is capturing them."""
        # a confirm card owns Esc (and swallows the shortcuts beneath it) until
        # it is answered — Esc must never skip past it to close the whole app
        if getattr(self, "_confirm_layer", None) is not None:
            if ev.keyval == Gdk.KEY_Escape:
                self._close_confirm()
            return True
        if ev.state & Gdk.ModifierType.CONTROL_MASK:
            if ev.keyval in (Gdk.KEY_o, Gdk.KEY_O):
                self._on_open()
                return True
        elif (not (ev.state & Gdk.ModifierType.MOD1_MASK)
                and self._menu_open is None
                and getattr(self, "_about_layer", None) is None):
            kv = ev.keyval
            if (kv in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete)
                    and self._media_path):
                self._on_trash()
                return True
            if self._orig_pixbuf is not None:
                if kv in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
                    self._on_zoom_in()
                    return True
                if kv in (Gdk.KEY_minus, Gdk.KEY_underscore,
                          Gdk.KEY_KP_Subtract):
                    self._on_zoom_out()
                    return True
                if kv in (Gdk.KEY_0, Gdk.KEY_KP_0):
                    self._fit_to_window()
                    return True
            if len(self._siblings) > 1:
                if kv in (Gdk.KEY_Left, Gdk.KEY_Page_Up):
                    self._on_prev()
                    return True
                if kv in (Gdk.KEY_Right, Gdk.KEY_Page_Down):
                    self._on_next()
                    return True
        # video fullscreen: Esc leaves it (claimed before the base treats Esc as
        # "close"); F toggles whenever the video stage is up.
        if (self._menu_open is None
                and getattr(self, "_about_layer", None) is None):
            if ev.keyval == Gdk.KEY_Escape and self._vfull:
                self._exit_video_fullscreen()
                return True
            if (ev.keyval in (Gdk.KEY_f, Gdk.KEY_F)
                    and not (ev.state & Gdk.ModifierType.CONTROL_MASK)
                    and self._video.get_visible()):
                self._toggle_video_fullscreen()
                return True
            # Standard player keys, which the video stage had none of: Space
            # (and K) play/pause, Left/Right jog 5s, Up/Down jog 30s, Home/End
            # jump to the ends.
            if self._video.get_visible() and self._player is not None:
                if ev.keyval in (Gdk.KEY_space, Gdk.KEY_k, Gdk.KEY_K):
                    self._on_video_toggle()
                    return True
                jog = {Gdk.KEY_Left: -5, Gdk.KEY_Right: 5,
                       Gdk.KEY_Down: -30, Gdk.KEY_Up: 30}.get(ev.keyval)
                if jog is not None:
                    self._jog_video(jog)
                    return True
                if ev.keyval in (Gdk.KEY_Home, Gdk.KEY_End):
                    self._seek_fraction(0.0 if ev.keyval == Gdk.KEY_Home else 0.999)
                    return True
        return super()._on_key(w, ev)

    def _on_video_click(self, _w, ev):
        """Single click toggles play/pause; double click toggles fullscreen.
        The double-click also undoes the play/pause its first click produced, so
        a double-click does not leave the clip in the opposite state."""
        if ev.type == Gdk.EventType._2BUTTON_PRESS and ev.button == 1:
            self._on_video_toggle()      # revert the single-click toggle
            self._toggle_video_fullscreen()
            return True
        if ev.type == Gdk.EventType.BUTTON_PRESS and ev.button == 1:
            self._on_video_toggle()
            return True
        return False

    def _jog_video(self, seconds):
        """Seek `seconds` relative to the current position, clamped to the clip."""
        if self._player is None or self._v_duration_ns <= 0:
            return
        try:
            ok, pos = self._player.query_position(Gst.Format.TIME)
            if not ok:
                return
            target = pos + int(seconds) * Gst.SECOND
            target = max(0, min(target, self._v_duration_ns - Gst.SECOND // 2))
            self._player.seek_simple(Gst.Format.TIME,
                                     Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                                     target)
        except Exception:
            pass

    def _seek_fraction(self, frac):
        """Seek to a fraction (0..1) of the clip."""
        if self._player is None or self._v_duration_ns <= 0:
            return
        try:
            target = int(max(0.0, min(1.0, frac)) * self._v_duration_ns)
            self._player.seek_simple(Gst.Format.TIME,
                                     Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                                     target)
        except Exception:
            pass

    # -- video fullscreen --
    def _menubar_widget(self):
        """The nbapp menubar (packed first in the window root, above content)."""
        try:
            root = self.content.get_parent()
            kids = root.get_children() if root is not None else []
            return kids[0] if kids else None
        except Exception:
            return None

    def _toggle_video_fullscreen(self):
        if self._vfull:
            self._exit_video_fullscreen()
        else:
            self._enter_video_fullscreen()

    def _enter_video_fullscreen(self):
        """Hide all chrome so the video fills the screen edge to edge. The window
        is already fullscreen; we just collapse the menubar, the info panel and
        the filmstrip, leaving the video stage (and its transport) to expand."""
        if not self._video.get_visible():
            return
        self._vfull = True
        # Everything that is not the picture goes: the menubar, the Open/rotate
        # TOOLBAR (it used to stay up, so "fullscreen" still had a file-chooser
        # strip across the top), the info panel and the filmstrip. Only the
        # video stage and its transport remain.
        for w in (self._menubar_widget(), getattr(self, "_toolbar_w", None),
                  getattr(self, "_info_w", None),
                  getattr(self, "_film_w", None)):
            if w is not None:
                w.hide()
        if hasattr(self, "_v_full_btn"):
            self._v_full_btn.set_label(_t("Exit Fullscreen"))
            self._v_full_btn.set_tooltip_text(_t("Leave fullscreen (Esc)"))

    def _exit_video_fullscreen(self):
        self._vfull = False
        bar = self._menubar_widget()
        if bar is not None:
            bar.show()
        for w in (getattr(self, "_toolbar_w", None),
                  getattr(self, "_info_w", None),
                  getattr(self, "_film_w", None)):
            if w is not None:
                w.show()
        if hasattr(self, "_v_full_btn"):
            self._v_full_btn.set_label(_t("Fullscreen"))
            self._v_full_btn.set_tooltip_text(_t("Fullscreen video (F)"))

    # -- menus --
    def _find_widget(self, cls, root=None):
        """First descendant of the content area whose style context carries
        CSS class `cls`, else None. Crash-safe recursive tree walk."""
        if root is None:
            root = self.content
        try:
            if root.get_style_context().has_class(cls):
                return root
        except Exception:
            pass
        if isinstance(root, Gtk.Container):
            for child in root.get_children():
                found = self._find_widget(cls, child)
                if found is not None:
                    return found
        return None

    def _toggle_widget(self, cls):
        """Flip the visibility of the first widget carrying `cls`; no-op if
        it isn't found (nothing to toggle, never crashes)."""
        w = self._find_widget(cls)
        if w is not None:
            w.set_visible(not w.get_visible())

    def menu_items(self, name):
        if name == "File":
            # The app's Open action ahead of the inherited Close. Move to Trash
            # is only offered when there is a file on screen to move.
            return [("Open…", lambda: self._on_open(None)),
                    ("Move to Trash",
                     (lambda: self._on_trash(None)) if self._media_path
                     else None),
                    nbapp.SEP] + super().menu_items(name)
        if name == "View":
            info = self._find_widget("infopanel")
            strip = self._find_widget("filmstrip")
            info_vis = info.get_visible() if info is not None else True
            strip_vis = strip.get_visible() if strip is not None else True
            return [
                (("Hide Info Panel" if info_vis else "Show Info Panel"),
                 lambda: self._toggle_widget("infopanel")),
                (("Hide Filmstrip" if strip_vis else "Show Filmstrip"),
                 lambda: self._toggle_widget("filmstrip")),
            ]
        return super().menu_items(name)

    def _install_css(self):
        css = b"""
        /* Toolbar continues the menubar tone; a soft hairline divides it
           from the stage below. */
        .toolbar { background: #F4F2EC; border-bottom: 1px solid #D7D2C5;
                   padding: 0 18px; }
        .toolbar * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        /* Open: the one labelled control, an outlined paper button. */
        .openbtn { padding: 0 16px; min-height: 32px; border: 1px solid #C9C4B6;
                   background: #FCFBF8; border-radius: 2px; box-shadow: none;
                   font-size: 13.5px; font-weight: 600; color: #1A1916; }
        .openbtn:hover { background: #EFEBE0; }
        /* Tool buttons: flat pictographic icons, no chrome; a calm hover only. */
        .toolbtn { min-width: 32px; min-height: 32px; padding: 0;
                   border: none; background: transparent;
                   border-radius: 2px; box-shadow: none; }
        .toolbtn:hover { background: #EAE5D9; }
        .toolbtn:disabled { border: none; background: transparent; }
        /* the running-slideshow button: a faint red cell behind its red glyph,
           the one active accent among the tools (never decorative). */
        .toolbtn.active { background: #FBEFEC; }
        .toolbtn.active:hover { background: #F7E3DD; }
        .zoompct { font-size: 12.5px; color: #9A9484; }
        /* the zoom percentage is also the Fit-to-window button: it reads as
           plain text but takes the same calm hover as the other tools. */
        .fitbtn { min-width: 44px; padding: 0 4px; }
        .vsep { color: #D7D2C5; min-width: 1px; margin: 0 6px; }

        /* Viewing stage: a subtly recessed papertone mat. The image scroller and
           its viewport share the mat tone so a fit image sits on it seamlessly. */
        .stage { background: #F1EEE6; }
        .stage * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .stage-title { font-size: 16px; font-weight: 600; color: #6E695E;
                       margin-top: 4px; }
        .stage-sub { font-size: 13.5px; color: #9A9484; }
        .imgscroll, .imgscroll viewport { background-color: #F1EEE6; }

        /* Video surface: a dark stage for the frame, with a paper transport bar
           beneath (play/pause, timecodes, seek). */
        .videobox { background: #16150F; }
        .vtransport { background: #F4F2EC; border-top: 1px solid #D7D2C5;
                      padding: 8px 18px; }
        .vtransport * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .vtimecode { font-size: 12.5px; color: #6E695E; }

        .infopanel { background: #F4F2EC; border-left: 1px solid #C9C4B6;
                     padding: 28px 26px; }
        .infopanel * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .info-head { font-size: 11px; letter-spacing: 0.16em; color: #6E695E;
                     font-weight: 700; margin-bottom: 18px; }
        .info-row { padding: 12px 0; border-bottom: 1px solid #D7D2C5; }
        .info-key { font-size: 13.5px; color: #6E695E; }
        .info-val { font-size: 13.5px; color: #1A1916; }

        .filmstrip { background: #F4F2EC; border-top: 1px solid #D7D2C5; }
        /* Opaque rail tone: a transparent viewport window renders solid black
           with no compositor, so match the filmstrip surface instead. */
        .stripscroll, .stripscroll viewport { background-color: #F4F2EC; }
        .filmrow { padding: 8px 12px; }
        /* Each thumbnail sits in a warm-paper cell with a darker-beige border;
           the current image is selected in signage-red (the one active/selected
           accent in this viewer), never black. */
        .filmcell { min-width: 116px; min-height: 66px; padding: 2px;
                    margin: 0 4px; border: 1px solid #D7D2C5; background: #FCFBF8;
                    border-radius: 2px; box-shadow: none; }
        .filmcell:hover { border-color: #C9C4B6; background: #F4F2EC; }
        .filmcell.filmsel { border-color: #C8341E; background: #FBEFEC; }
        .strip-empty { font-size: 12.5px; color: #9A9484;
                       font-family: "Nimbus Sans","Helvetica",sans-serif; }

        /* In-window confirmation card (Move to Trash): the house pattern of
           warm paper, a taupe hairline, and the one signage red reserved for
           the single destructive action. */
        .vdlg { background: #F8F7F2; border: 1px solid #C4BFB1;
                padding: 24px 28px; }
        .vdlg * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .vdlg-title { font-size: 17px; font-weight: 700; color: #1A1916; }
        .vdlg-body { font-size: 13px; color: #6E695E; }
        .vdlg-btn { min-height: 34px; padding: 0 18px; border-radius: 2px;
                    border: 1px solid #C9C4B6; background: #FCFBF8;
                    color: #1A1916; font-size: 14px; box-shadow: none; }
        .vdlg-btn:hover { background: #ECE7DB; }
        .vdlg-primary { background: #C8341E; border-color: #C8341E;
                        color: #FCFBF8; font-weight: 600; }
        .vdlg-primary:hover { background: #B12C18; border-color: #B12C18; }
        """
        prov = Gtk.CssProvider()
        prov.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), prov,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)


if __name__ == "__main__":
    nbapp.run(MediaViewer)
