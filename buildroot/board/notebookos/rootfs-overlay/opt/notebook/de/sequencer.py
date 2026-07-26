#!/usr/bin/env python3
"""
Sequencer — Notebook OS multitrack tone sequencer (native GTK).

A transport deck (rewind / fast-forward / stop / play / record) above an
8-track timeline. Each track carries an instrument-voice selector, arm, mute,
solo, gain and pan; a playhead sweeps the lanes and recording an armed track
commits a take clip. Global controls: tempo (BPM), metronome, pattern length,
varispeed pitch and master level.

Projects (tracks + mix + takes) are read/written as JSON via the File menu
under $NB_HOME/Documents. A rolling autosave to CFG_DIR/sequencer.json provides
session recovery. Ships empty — no takes, nothing armed.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, GLib, Pango, PangoCairo  # noqa: E402

# ---- media engine (GUARDED) ------------------------------------------------
# GStreamer (and its 'app'/appsrc element) is only guaranteed on the built
# guest; the host that runs construct_all.py / the selftests may not have it.
# Import it defensively so this app still CONSTRUCTS and shows its empty state
# with no sound. gi.require_version is wrapped too (an unknown version raises
# ValueError). Nothing here touches Gst at import time — ToneEngine.start()
# does, lazily, on the first Play/Record.
GST_OK = False
try:
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    GST_OK = True
except (ValueError, ImportError):
    Gst = None

import math
import os
import re
import json
import array
import shutil
import subprocess
import threading
import time

import nbapp
import nbpicker
import nbicons
from nbi18n import _t  # noqa: E402

TRACKS = 8            # fixed track count
# How many arrangement steps Undo can walk back. A frame is a small dict of
# numbers and names (see _arrangement), so the history costs nothing worth
# measuring even at this depth.
UNDO_DEPTH = 40
DEFAULT_LEN = 120.0   # default pattern/tape length in seconds
BEATS_PER_BAR = 4
BPM_MIN, BPM_MAX = 40, 240
# pattern-length steps the LENGTH control cycles through (seconds)
LEN_CHOICES = (30.0, 60.0, 90.0, 120.0, 180.0, 240.0)
# ...and the same control expressed MUSICALLY. A sequencer is arranged in bars,
# not minutes: at 120 BPM in 4/4 a bar is two seconds, so a 32-bar section is a
# fixed musical length whatever the tempo, while "60 seconds" is a different
# amount of music at every tempo. The tape length is stored in seconds (the
# transport and every clip are in seconds), but it is CHOSEN and DISPLAYED in
# bars, and re-derived whenever the tempo changes so the arrangement keeps its
# musical length.
BAR_CHOICES = (8, 16, 24, 32, 48, 64, 96, 128)
# per-track instrument voices. Each maps to a distinct register / envelope in
# the tone engine (name, octave multiplier, percussive?), so cycling the
# selector always changes the sound — no two voices are silent duplicates.
# These are synth voices, not audio-capture inputs: the engine generates every
# tone itself (there is no microphone / line-in path).
# The one non-synth input: a real capture device (built-in, USB mic, or a USB
# audio interface). A track set to Mic is not synthesised — arming it and
# hitting Record captures audio to a WAV that plays back with the arrangement.
MIC = "Mic"
# Capture format: 48 kHz matches the synth engine's SR, 16-bit mono is what a
# single mic or interface input actually provides and keeps the files small.
CAP_RATE = 48000
CAP_FMT = "S16_LE"

VOICES = (
    ("Synth", 1.0, False),
    ("Keys",  2.0, False),
    ("Lead",  1.5, False),
    ("Bell",  3.0, False),
    ("Bass",  0.5, False),
    ("Drums", 1.0, True),
)
INPUTS = tuple(v[0] for v in VOICES) + (MIC,)        # valid inputs (+ capture)
_VOICE = {n: (o, p) for (n, o, p) in VOICES}         # name -> (octave, perc)

# ---- tone engine tuning ----
SR = 48000            # synth sample rate (mono, S16LE)
SR_KHZ = "%d kHz" % (SR // 1000)                     # honest rate label (UI)
BLOCK = 2048          # samples pushed per appsrc need-data (≈23 blocks/sec)
_TSIZE = 2048         # sine wavetable length (power of two, phase-masked)
# the 8 lanes voice a consonant stack (C major 6/9) so overlapping takes chord
TRACK_HZ = (130.81, 164.81, 196.00, 220.00, 261.63, 329.63, 392.00, 440.00)

# persistence — the user's tracks + mix live here (widgets.py/tasks.py pattern)
HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
CFG_DIR = os.path.join(HOME, ".config", "notebook")
CFG_FILE = os.path.join(CFG_DIR, "sequencer.json")
# Recorded takes live beside the project data so a saved project's audio
# travels with it.
TAKES_DIR = os.path.join(CFG_DIR, "takes")
# File ▸ Open/Save projects live under Documents; CFG_FILE is session recovery
PROJ_DIR = os.path.join(HOME, "Documents")

# palette — papertone, ink, and the single signage red (see design language)
INK = (0x1A / 255, 0x19 / 255, 0x16 / 255)      # ink
RED = (0xC8 / 255, 0x34 / 255, 0x1E / 255)      # signage red — active / alert only
MUTED = (0x6E / 255, 0x69 / 255, 0x5E / 255)    # muted text
FAINT = (0x9A / 255, 0x94 / 255, 0x84 / 255)    # faint text / placeholders
VU_OFF = (0xDC / 255, 0xD5 / 255, 0xC4 / 255)   # unlit meter segment
CENTER = (0xD7 / 255, 0xD2 / 255, 0xC5 / 255)   # soft hairline (lane centre)
HAIR = (0xC9 / 255, 0xC4 / 255, 0xB6 / 255)     # strong hairline
RAIL = (0xF1 / 255, 0xEE / 255, 0xE6 / 255)     # rail / deck / track-head surface
SURF = (0xFC / 255, 0xFB / 255, 0xF8 / 255)     # page / control base surface


def clip_parts(c):
    """A clip is (start, end) for a synth track and (start, end, wav) for a
    captured one. One accessor so every reader handles both."""
    try:
        if len(c) >= 3:
            return float(c[0]), float(c[1]), (c[2] or None)
        return float(c[0]), float(c[1]), None
    except Exception:
        return 0.0, 0.0, None


def _have(cmd):
    return shutil.which(cmd) is not None


def capture_devices():
    """Every ALSA CAPTURE device, as [(alsa_name, label), ...].

    Parsed from `arecord -l`, whose lines look like

        card 1: Device [USB Audio Device], device 0: USB Audio [USB Audio]

    so a USB microphone or a USB audio interface shows up exactly like the
    built-in codec does — the kernel has CONFIG_SND_USB_AUDIO=y, so any
    USB-audio-class device enumerates as its own card and needs no extra
    driver. The returned name is "hw:<card>,<device>", which addresses that
    input directly rather than whatever ALSA happens to call "default".

    Always returns at least the system default, and never raises: with no
    arecord, no sound card, or a device that appears mid-session, the caller
    still gets a usable list."""
    devs = []
    if _have("arecord"):
        try:
            out = subprocess.run(["arecord", "-l"], capture_output=True,
                                 text=True, timeout=4).stdout
            for ln in out.splitlines():
                m = re.match(r"card (\d+): \S+ \[([^\]]*)\], "
                             r"device (\d+): [^\[]*\[([^\]]*)\]", ln.strip())
                if not m:
                    continue
                card, cname, dev, dname = m.groups()
                label = cname.strip()
                if dname.strip() and dname.strip().lower() != label.lower():
                    label = "%s - %s" % (label, dname.strip())
                devs.append(("hw:%s,%s" % (card, dev), label))
        except Exception:
            pass
    # The system default last: a named device is the honest choice when one
    # exists, but there must always be something selectable.
    devs.append(("default", "System default input"))
    return devs


class Recorder:
    """Captures one take to a WAV with `arecord`.

    A subprocess rather than a GStreamer pipeline: arecord is already on the
    image, speaks straight to ALSA (so a USB interface works with no extra
    plumbing), and writing a real .wav header means the take is a normal file
    the rest of the OS can open."""

    def __init__(self):
        self.proc = None
        self.path = None

    def start(self, device, path):
        """Begin capturing to `path`. Returns (ok, plain-English message).

        The messages are what the user reads in the status line, so they say
        what happened in the app's own terms — never the name of the recording
        program or a raw system error."""
        if not _have("arecord"):
            return False, "Recording isn't available on this computer"
        self.stop()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        except OSError:
            return False, "Couldn't make a folder to keep the recording in"
        cmd = ["arecord", "-D", device or "default",
               "-f", CAP_FMT, "-r", str(CAP_RATE), "-c", "1",
               "-t", "wav", path]
        try:
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except OSError:
            self.proc = None
            return False, "Couldn't start recording"
        self.path = path
        return True, ""

    def stop(self):
        """End the take. Returns the WAV path if one was actually written."""
        p, path = self.proc, self.path
        self.proc, self.path = None, None
        if p is None:
            return None
        try:
            p.terminate()
            try:
                p.wait(timeout=3)
            except Exception:
                p.kill()
                p.wait(timeout=2)
        except Exception:
            pass
        # A WAV with only a header (44 bytes) captured nothing — report it as
        # no take rather than leaving a silent clip the user cannot explain.
        try:
            if path and os.path.getsize(path) > 1024:
                return path
            if path and os.path.exists(path):
                os.unlink(path)
        except OSError:
            pass
        return None

    def failed_early(self):
        """True when the capture died on its own (a busy or missing input)
        rather than being stopped by us. The recorder's raw error text is
        deliberately NOT surfaced — it is program output, not something to show
        someone who just wanted to record a take."""
        p = self.proc
        if p is None or p.poll() is None:
            return False
        try:
            p.stderr.read()          # drain, so the child can't block on a pipe
        except Exception:
            pass
        return True


class Player:
    """Plays recorded takes back with `aplay`, one process per sounding clip."""

    def __init__(self):
        self.procs = []

    def play(self, path, seek_s=0.0):
        if not (_have("aplay") and path and os.path.exists(path)):
            return
        # aplay cannot seek, so a clip entered mid-way is skipped rather than
        # played from its start (which would sound out of time).
        if seek_s > 0.05:
            return
        try:
            self.procs.append(subprocess.Popen(
                ["aplay", "-q", path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        except OSError:
            pass

    def stop(self):
        for p in self.procs:
            try:
                p.terminate()
            except Exception:
                pass
        self.procs = [p for p in self.procs if p.poll() is None]
        for p in self.procs:
            try:
                p.kill()
            except Exception:
                pass
        self.procs = []

    def reap(self):
        self.procs = [p for p in self.procs if p.poll() is None]


class VU(Gtk.DrawingArea):
    """A horizontal segment level meter."""
    def __init__(self, count, seg_w=7, seg_h=14, gap=2, bg=RAIL):
        super().__init__()
        self.count = count
        self.level = 0.0
        self.seg_w = seg_w
        self.seg_h = seg_h
        self.gap = gap
        self.bg = bg
        self.set_size_request(count * (seg_w + gap) - gap, seg_h)
        self.set_valign(Gtk.Align.CENTER)
        self.connect("draw", self._draw)

    def set_level(self, lv):
        lv = max(0.0, min(1.0, lv))
        if abs(lv - self.level) > 0.001:
            self.level = lv
            self.queue_draw()

    def _draw(self, w, cr):
        # opaque fill first (no-compositor: the inter-segment gaps and the strip
        # above/below the segments would render black on the framebuffer)
        a = w.get_allocation()
        cr.set_source_rgb(*self.bg)
        cr.rectangle(0, 0, a.width, a.height)
        cr.fill()
        for i in range(self.count):
            on = self.level > i / self.count
            hot = i >= self.count - 2
            col = ((RED if hot else INK) if on else VU_OFF)
            cr.set_source_rgb(*col)
            _rrect(cr, i * (self.seg_w + self.gap), 0, self.seg_w, self.seg_h, 1)
            cr.fill()


class Dot(Gtk.DrawingArea):
    def __init__(self, size=8, bg=RAIL):
        super().__init__()
        self.color = MUTED
        self.bg = bg
        self.set_size_request(size, size)
        self.set_valign(Gtk.Align.CENTER)
        self.connect("draw", self._draw)

    def set_color(self, c):
        if c != self.color:
            self.color = c
            self.queue_draw()

    def set_bg(self, c):
        if c != self.bg:
            self.bg = c
            self.queue_draw()

    def _draw(self, w, cr):
        a = w.get_allocation()
        # opaque fill first — the square corners around the circle would render
        # black without a compositor
        cr.set_source_rgb(*self.bg)
        cr.rectangle(0, 0, a.width, a.height)
        cr.fill()
        r = min(a.width, a.height) / 2
        cr.set_source_rgb(*self.color)
        cr.arc(a.width / 2, a.height / 2, r, 0, 2 * math.pi)
        cr.fill()


class Lane(Gtk.DrawingArea):
    """One track's tape lane: clips, rec region, playhead, empty state.

    On the GPU-less hardware framebuffer the CPU rasterises every expose, so the
    static content (lane background, centre line, committed clips and the
    empty-state label) is rendered once into a cached ImageSurface and only
    rebuilt when it actually changes — a take commits, mute/solo/length/arm
    change, or the lane is resized (the cache key carries all of these plus
    W×H). Each frame then costs one surface blit plus the cheap moving overlay
    (the growing rec region + the playhead). Crucially the per-tick playhead
    sweep goes through sync(), which invalidates only the thin strip the head
    moves across (queue_draw_area) rather than the whole lane — so a running
    transport touches a few hundred pixels per tick instead of re-tracing every
    clip, stroke and glyph across eight full-width lanes ten times a second."""
    DRAG_PX = 6      # a press that moves less than this is a seek, not a part

    def __init__(self, app, idx):
        super().__init__()
        self.app = app
        self.idx = idx
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                        | Gdk.EventMask.BUTTON_RELEASE_MASK
                        | Gdk.EventMask.BUTTON1_MOTION_MASK)
        self.connect("draw", self._draw)
        self.connect("button-press-event", self._click)
        self.connect("motion-notify-event", self._motion)
        self.connect("button-release-event", self._release)
        self.set_tooltip_text(
            _t("Drag to lay down a part  ·  click to move the playhead  ·  "
               "right-click a part to remove it"))
        self._cache = None       # (static_key, ImageSurface) or None
        self._play_px = None      # last drawn playhead x, for strip invalidation
        self._press_x = None      # where a drag started, in widget pixels
        self._drag = None         # (t0, t1) seconds being dragged out, or None

    def _time_at(self, x):
        """Tape position in seconds under widget x."""
        a = self.get_allocation()
        if a.width <= 0:
            return 0.0
        return max(0.0, min(1.0, x / a.width)) * self.app.length

    def _click(self, w, ev):
        # A seek while recording would move the playhead mid-take and
        # corrupt/silently discard the in-progress take — ignore playhead
        # moves during recording so an accidental click can't ruin a take.
        if self.app.transport == "rec":
            return True
        if ev.button == 3:
            self.app.remove_part(self.idx, self._time_at(ev.x))
            return True
        if ev.button != 1:
            return True
        self._press_x = ev.x
        self._drag = None
        self.app.pos = self._time_at(ev.x)
        self.app.refresh()
        return True

    def _motion(self, w, ev):
        """A held drag sketches a part; anything shorter stays a plain seek."""
        if self._press_x is None or self.app.transport == "rec":
            return False
        if self._drag is None and abs(ev.x - self._press_x) < self.DRAG_PX:
            return False
        self._drag = (self._time_at(self._press_x), self._time_at(ev.x))
        self.queue_draw()
        return True

    def _release(self, w, _ev):
        drag = self._drag
        self._press_x, self._drag = None, None
        if drag is None or self.app.transport == "rec":
            return False
        self.queue_draw()
        self.app.add_part(self.idx, min(drag), max(drag))
        return True

    def _static_key(self, W, H):
        """Everything the cached surface depends on. A mismatch means the
        surface is stale and both it and the whole lane must be repainted."""
        tk = self.app.tracks[self.idx]
        rec = (self.app.transport == "rec" and tk["armed"]
               and self.app.rec_start is not None)
        # the first lane carries the how-to-record hint until the tape has
        # anything on it, so whether the WHOLE project is empty is part of its
        # static content, not just this track's clips
        return (W, H, self.app._audible(tk), round(self.app.length, 3),
                tuple(tk["clips"]), bool(tk["armed"]), rec,
                self.idx == 0 and self.app.is_empty())

    def _paint_static(self, cr, W, H):
        """Render the lane's slow-changing layer (everything except the moving
        rec region and playhead). Runs on a cache miss only, never per tick."""
        L = self.app.length
        tk = self.app.tracks[self.idx]
        audible = self.app._audible(tk)
        rec = (self.app.transport == "rec" and tk["armed"]
               and self.app.rec_start is not None)
        # background
        if not audible:
            cr.set_source_rgb(0xF1 / 255, 0xEE / 255, 0xE6 / 255)
        else:
            cr.set_source_rgb(0xFC / 255, 0xFB / 255, 0xF8 / 255)
        cr.rectangle(0, 0, W, H)
        cr.fill()
        # centre hairline
        cr.set_source_rgb(*CENTER)
        cr.rectangle(0, int(H / 2), W, 1)
        cr.fill()
        top = H * 0.14
        ch = H * 0.72
        # committed clips
        for _c in tk["clips"]:
            s, e, _wav = clip_parts(_c)
            x = s / L * W
            cw = max(3, (e - s) / L * W)
            if not audible:
                cr.set_source_rgb(0xDD / 255, 0xD6 / 255, 0xC6 / 255)
            else:
                cr.set_source_rgb(0xCB / 255, 0xBF / 255, 0xA0 / 255)
            _rrect(cr, x, top, cw, ch, 2)
            cr.fill()
            cr.set_source_rgba(*INK, 0.42)
            cr.set_line_width(1)
            _rrect(cr, x + 0.5, top + 0.5, cw - 1, ch - 1, 2)
            cr.stroke()
            cr.set_source_rgba(*INK, 0.28)
            cr.rectangle(x + 6, int(H / 2), max(0, cw - 12), 1)
            cr.fill()
        # empty label (only when this lane has no takes and isn't recording).
        # On a brand-new tape the top lane says HOW to fill it — eight lanes
        # all reading "No takes" told a first-time user nothing about what to
        # do next.
        if not tk["clips"] and not rec:
            if tk["armed"]:
                label = _t("Armed — standby")
            elif self.idx == 0 and self.app.is_empty():
                # the fastest route to a sound the user made, first; recording
                # is the other way in and stays named beside it
                label = _t("Drag across a lane to lay down a part"
                           "     or arm REC and record one")
            else:
                label = _t("No takes")
            cr.set_source_rgb(*FAINT)
            # Pango, not cairo's toy font API. The toy API resolves ONE face and
            # draws .notdef for anything that face lacks, so the moment this
            # hint became translatable it would have come out as empty boxes in
            # Chinese. Pango falls back per glyph, so it reads in every
            # language. Painted on a cache miss only, never per frame.
            layout = PangoCairo.create_layout(cr)
            fd = Pango.FontDescription("Nimbus Sans")
            fd.set_absolute_size(11.5 * Pango.SCALE)
            layout.set_font_description(fd)
            layout.set_text(label, -1)
            cr.move_to(14, 11)
            PangoCairo.show_layout(cr, layout)

    def _paint_overlay(self, cr, W, H):
        """The cheap moving layer, blitted on top of the cached surface every
        frame: the active rec region (its leading edge grows with the playhead)
        and the playhead itself. Records the drawn playhead x for sync()."""
        L = self.app.length
        tk = self.app.tracks[self.idx]
        top = H * 0.14
        ch = H * 0.72
        rec = (self.app.transport == "rec" and tk["armed"]
               and self.app.rec_start is not None)
        if rec:
            x = self.app.rec_start / L * W
            cw = max(2, (self.app.pos - self.app.rec_start) / L * W)
            cr.set_source_rgba(*RED, 0.12)
            cr.rectangle(x, top, cw, ch)
            cr.fill()
            cr.set_source_rgb(*RED)
            cr.set_line_width(1)
            cr.set_dash([3, 3])
            cr.rectangle(x + 0.5, top + 0.5, cw - 1, ch - 1)
            cr.stroke()
            cr.set_dash([])
        # the part being dragged out, shown already snapped to the bar grid so
        # what the user sees is exactly what they will get. Ink, not red —
        # signage red belongs to recording and alerts.
        if self._drag is not None:
            s, e = self.app.snap_part(min(self._drag), max(self._drag))
            x = s / L * W
            cw = max(2, (e - s) / L * W)
            cr.set_source_rgba(*INK, 0.10)
            cr.rectangle(x, top, cw, ch)
            cr.fill()
            cr.set_source_rgb(*INK)
            cr.set_line_width(1)
            cr.set_dash([3, 3])
            cr.rectangle(x + 0.5, top + 0.5, cw - 1, ch - 1)
            cr.stroke()
            cr.set_dash([])
        # playhead
        px = self.app.pos / L * W
        cr.set_source_rgb(*RED)
        cr.rectangle(round(px) - 1, 0, 2, H)
        cr.fill()
        self._play_px = px

    def _draw(self, w, cr):
        a = w.get_allocation()
        W, H = a.width, a.height
        if W < 4 or H < 4:            # not-yet / degenerately allocated
            return False
        cache = self._cache
        key = self._static_key(W, H)
        if not (cache and cache[0] == key):
            try:
                import cairo
                # opaque static layer → RGB24 (no alpha channel to blit)
                surf = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
                self._paint_static(cairo.Context(surf), W, H)
                surf.flush()
                cache = self._cache = (key, surf)
            except Exception:
                # a surface hiccup must never blank the lane — paint direct
                self._cache = None
                try:
                    self._paint_static(cr, W, H)
                    self._paint_overlay(cr, W, H)
                except Exception:
                    pass
                return False
        cr.set_source_surface(cache[1], 0, 0)
        cr.paint()
        self._paint_overlay(cr, W, H)
        return False

    def sync(self):
        """Per-refresh lane update. If the static content changed, repaint the
        whole lane (rebuilds the cache); otherwise invalidate only the narrow
        strip the playhead — and the growing rec region's leading edge — sweep
        through, so an idle-but-running transport never re-rasterises the grid."""
        a = self.get_allocation()
        W, H = a.width, a.height
        if W < 4 or H < 4:
            return
        cache = self._cache
        if not (cache and cache[0] == self._static_key(W, H)):
            self.queue_draw()            # static content changed → full repaint
            return
        L = self.app.length
        new_px = self.app.pos / L * W
        old_px = self._play_px if self._play_px is not None else new_px
        lo = int(max(0, min(old_px, new_px) - 3))
        hi = int(min(W, max(old_px, new_px) + 3))
        self.queue_draw_area(lo, 0, max(1, hi - lo), H)


class Ruler(Gtk.DrawingArea):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.set_hexpand(True)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("draw", self._draw)
        self.connect("button-press-event", self._click)

    def _axis(self):
        """The LANES' time axis, in this widget's own coordinates: (x0, width).

        The ruler measures the tape, so it must use the lanes' geometry and not
        its own: a lane starts after the track head and stops short of the
        stack's scrollbar, and off by even a few pixels the numbered grid drifts
        away from the clips it is numbering (and a click on bar 33 seeks
        somewhere a click on the lane does not). Falls back to this widget's own
        width before the lanes are laid out."""
        a = self.get_allocation()
        lanes = getattr(self.app, "lanes", None)
        if lanes:
            la = lanes[0].get_allocation()
            xy = lanes[0].translate_coordinates(self, 0, 0)
            if la.width > 4 and xy is not None:
                return xy[0], la.width
        return 0, a.width

    def _click(self, w, ev):
        # A seek while recording would move the playhead mid-take and
        # corrupt/silently discard the in-progress take — ignore playhead
        # moves during recording so an accidental click can't ruin a take.
        if self.app.transport == "rec":
            return True
        x0, aw = self._axis()
        if aw <= 0:
            return True
        self.app.pos = max(0.0, min(1.0, (ev.x - x0) / aw)) * self.app.length
        self.app.refresh()
        return True

    def _draw(self, w, cr):
        a = w.get_allocation()
        W, H = a.width, a.height
        x0, aw = self._axis()
        L = self.app.length
        # opaque fill first — an unpainted DrawingArea window renders black on
        # the no-compositor framebuffer (matches the .rulerrow rail surface)
        cr.set_source_rgb(*RAIL)
        cr.rectangle(0, 0, W, H)
        cr.fill()
        cr.select_font_face("Nimbus Sans", cairo_slant(), cairo_weight())
        cr.set_font_size(10.5)
        # A BAR ruler, not a clock: an arrangement is read in bars and beats, so
        # the grid lines fall on musical divisions and follow the tempo. Bars are
        # numbered; beats inside a bar get a short tick. When the arrangement is
        # long enough that every bar would collide, only every 2nd/4th/8th bar is
        # labelled — the ticks stay, so the grid is still countable.
        spb = self.app.sec_per_bar()
        if spb <= 0 or L <= 0:
            return
        bars = max(1, int(round(L / spb)))
        px_per_bar = aw / float(bars)
        step = 1
        for cand in (1, 2, 4, 8, 16, 32):
            if px_per_bar * cand >= 46:      # room for a "123" label
                step = cand
                break
        beat_w = px_per_bar / BEATS_PER_BAR
        for b in range(bars + 1):
            x = x0 + b * px_per_bar
            if x > W:
                break
            labelled = (b % step == 0)
            cr.set_source_rgb(*(HAIR if labelled else CENTER))
            cr.rectangle(round(x), 5, 1, 20 if labelled else 12)
            cr.fill()
            # beat ticks, only when they are far enough apart to read
            if beat_w >= 9 and b < bars:
                for k in range(1, BEATS_PER_BAR):
                    bx = x + k * beat_w
                    if bx >= W:
                        break
                    cr.set_source_rgb(*CENTER)
                    cr.rectangle(round(bx), 17, 1, 8)
                    cr.fill()
            if labelled and b < bars:
                cr.set_source_rgb(*MUTED)
                cr.move_to(x + 5, 19)
                cr.show_text("%d" % (b + 1))     # bars count from 1


def _rrect(cr, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def cairo_slant():
    import cairo
    return cairo.FONT_SLANT_NORMAL


def cairo_weight():
    import cairo
    return cairo.FONT_WEIGHT_NORMAL


class ToneEngine:
    """A small software synth voiced through GStreamer — the real sound engine.

    Sine voices are summed in pure Python and streamed as raw S16LE PCM into an
    ``appsrc ! audioconvert ! audioresample ! autoaudiosink`` pipeline. appsrc is
    the one signal generator guaranteed in the guest's plugin set (the 'app'
    plugin is built; audiotestsrc is NOT), so we generate the samples ourselves.
    Each note is a short pulsed tone; the sequencer fires them on the beat, which
    also gives the audible metronome and the play-back of recorded takes.

    Everything is a silent no-op unless start() succeeds, so if Gst is missing
    (host / selftest) or the audio device won't open, ``available`` stays False
    and the visual sequencer keeps working untouched.
    """
    # a single shared sine table; pure-Python, safe to build at import time
    _TABLE = [math.sin(2 * math.pi * i / _TSIZE) for i in range(_TSIZE)]
    _SILENCE = bytes(BLOCK * 2)

    def __init__(self):
        self.available = False    # True once the pipeline is actually streaming
        self.failed = False       # True once we tried and Gst / the sink refused
        self._pipe = None
        self._src = None
        self._started = False
        self._lock = threading.Lock()
        # each voice: [freq, phase, pos_samples, n_samples, amp, perc]
        self._voices = []

    # -- lifecycle -------------------------------------------------------
    def start(self):
        """Build + play the pipeline on first use (idempotent). Latches
        ``failed`` and returns False on any error so callers fall to silence."""
        if self._started:
            return self.available
        self._started = True
        if not GST_OK:
            self.failed = True
            return False
        try:
            Gst.init(None)
            self._pipe = Gst.parse_launch(
                "appsrc name=src is-live=true format=time do-timestamp=true "
                "emit-signals=true block=false ! audioconvert ! audioresample "
                "! autoaudiosink")
            self._src = self._pipe.get_by_name("src")
            self._src.set_property("caps", Gst.Caps.from_string(
                "audio/x-raw,format=S16LE,layout=interleaved,"
                "rate=%d,channels=1" % SR))
            self._src.set_property("max-bytes", 4 * BLOCK * 2)
            self._src.connect("need-data", self._need_data)
            bus = self._pipe.get_bus()
            bus.add_signal_watch()
            bus.connect("message::error", self._on_bus_error)
            if self._pipe.set_state(
                    Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("pipeline refused to start")
            self.available = True
            return True
        except Exception:
            self._teardown()
            self.failed = True
            return False

    def _on_bus_error(self, _bus, _msg):
        # the sink can fail asynchronously (no audio device) — drop to silence
        # and let the UI surface the neutral 'engine unavailable' note
        self._teardown()
        self.available = False
        self.failed = True

    def _teardown(self):
        try:
            if self._pipe is not None:
                self._pipe.set_state(Gst.State.NULL)
        except Exception:
            pass
        with self._lock:
            self._voices = []

    def shutdown(self):
        self._teardown()

    # -- voicing ---------------------------------------------------------
    def note(self, freq, dur, amp, perc=False):
        """Queue one short tone: freq Hz, dur real-seconds, amp 0..1."""
        if not self.available or amp <= 0.0 or freq <= 0 or dur <= 0:
            return
        n = int(dur * SR)
        if n < 1:
            return
        with self._lock:
            if len(self._voices) < 24:   # cap polyphony so render stays cheap
                self._voices.append(
                    [float(freq), 0.0, 0, n, min(1.0, float(amp)), bool(perc)])

    def silence(self):
        """Drop every ringing voice immediately (transport stop / seek)."""
        with self._lock:
            self._voices = []

    # -- streaming (runs on the appsrc streaming thread) -----------------
    def _need_data(self, src, _length):
        with self._lock:
            data = self._render(self._voices) if self._voices else self._SILENCE
        buf = Gst.Buffer.new_wrapped(data)
        try:
            src.emit("push-buffer", buf)
        except Exception:
            pass

    def _render(self, voices):
        """Sum the active voices into one BLOCK of S16LE PCM, advancing and
        pruning each. Called under the lock; keep it tight."""
        table = self._TABLE
        mask = _TSIZE - 1
        out = array.array("h", self._SILENCE)
        atk = 96                          # ~2 ms click-free attack
        for v in list(voices):
            freq, phase, pos, n, amp, perc = v
            inc = freq * _TSIZE / SR
            rel = max(1, n // 8)          # tonic release ramp
            peak = amp * 9000.0
            body = max(1, n - atk)
            for i in range(BLOCK):
                if pos >= n:
                    break
                if pos < atk:
                    env = pos / atk
                elif perc:
                    env = 1.0 - (pos - atk) / body   # drums decay across note
                elif pos > n - rel:
                    env = (n - pos) / rel
                else:
                    env = 1.0
                s = out[i] + int(table[int(phase) & mask] * peak * env)
                out[i] = -32768 if s < -32768 else (32767 if s > 32767 else s)
                phase += inc
                pos += 1
            v[1], v[2] = phase, pos
            if pos >= n:
                voices.remove(v)
        return out.tobytes()


class Sequencer(nbapp.AppWindow):
    app_name = "Sequencer"
    menus = ("File", "Edit", "Transport", "Track", "Input")

    def __init__(self):
        super().__init__()
        self._install_css()

        # ---- state ----
        self.transport = "stop"
        self.pos = 0.0
        self.rec_start = None
        self.tick = 0
        self._path = None       # current project file (File ▸ Save), or None
        self._loading = False   # guards value-changed handlers during sync
        self._save_timer = None    # pending debounced autosave, or None
        self._saved_timer = None   # transient 'Saved HH:MM' restore, or None
        self._prompt_layer = None  # open confirm-card overlay, if any
        self._runner_id = None     # 100ms transport tick source, when engaged
        self._undo_stack = []      # arrangement snapshots, newest last
        self._redo_stack = []
        self._rendered = {}        # last-rendered values, so refresh() only
        #                            rewrites a widget when its value changed
        # real sound engine (lazily started on first Play/Record) + beat tracker
        self.engine = ToneEngine()
        self._last_beat = None
        # capture (Mic tracks) — see Recorder / Player / capture_devices
        self.recorder = Recorder()
        self.player = Player()
        self._cap_device = None      # ALSA name; None = first available
        self._played = set()         # clips already triggered this pass
        # tempo, metronome, length, pitch, master and the 8 tracks
        # (name/input/arm/mute/solo/gain/pan/clips) are restored from disk; a
        # fresh install yields the empty default tape.
        self._load_state()

        self.content.pack_start(self._transport_bar(), False, False, 0)
        self.content.pack_start(self._track_area(), True, True, 0)
        self.content.pack_start(self._status_bar(), False, False, 0)

        self.connect("key-press-event", self._on_space)
        # flush the mix on close so the last tweak or take is never lost
        self.connect("destroy", self._on_destroy)
        # the 100ms transport tick is armed lazily (only while play/rec/ff/rew is
        # engaged) and drops itself on stop — see _ensure_runner / _runner. The
        # app ships stopped, so nothing is scheduled until the user presses go.
        self._update_length_btn()
        self._update_proj()
        self.refresh()

    # ================= persistence =================
    def _default_tracks(self):
        """The ships-empty 8-track tape: no takes, nothing armed."""
        return [
            {"name": "Track %d" % (i + 1), "input": "Synth",
             "armed": False, "muted": False, "solo": False,
             "gain": 80, "pan": 50, "clips": []}
            for i in range(TRACKS)
        ]

    def _norm_track(self, i, t):
        """Coerce one saved track dict into the full in-memory shape."""
        base = self._default_tracks()[i]
        if not isinstance(t, dict):
            return base
        inp = t.get("input")
        if inp not in INPUTS:
            # accept a case-variant voice name; otherwise fall back to the
            # default voice (older Mic/Line capture labels land here honestly)
            low = str(inp).strip().lower()
            inp = next((v for v in INPUTS if v.lower() == low), base["input"])
        clips = []
        for c in (t.get("clips") or []):
            try:
                s, e = float(c[0]), float(c[1])
            except (TypeError, ValueError, IndexError):
                continue
            # a captured clip carries its WAV; drop the reference if the file
            # has gone so the clip degrades to a silent region rather than a
            # take that never sounds with no explanation.
            wav = None
            try:
                if len(c) >= 3 and isinstance(c[2], str) and os.path.exists(c[2]):
                    wav = c[2]
            except (TypeError, IndexError):
                wav = None
            s = max(0.0, min(self.length, s))
            e = max(0.0, min(self.length, e))
            if e - s > 0.001:
                clips.append((s, e, wav) if wav else (s, e))
        # cap the name so a hand-edited file with a runaway string can't blow
        # out the fixed-width track head; whitespace-only falls back to default
        name = str(t.get("name") or base["name"]).strip()[:48] or base["name"]
        return {
            "name": name,
            "input": inp,
            "armed": bool(t.get("armed")),
            "muted": bool(t.get("muted")),
            "solo": bool(t.get("solo")),
            "gain": _clampi(t.get("gain"), 0, 100, 80),
            "pan": _clampi(t.get("pan"), 0, 100, 50),
            "clips": clips,
        }

    def _apply(self, data):
        """Load project state from a dict, clamping every field so a hand-edited
        or foreign file can't wedge launch. Missing keys fall back to defaults;
        an empty dict yields a fresh, empty project."""
        if not isinstance(data, dict):
            data = {}
        self.length = _clampf(data.get("length"), 10.0, 600.0, DEFAULT_LEN)
        self.bpm = _clampi(data.get("bpm"), BPM_MIN, BPM_MAX, 120)
        cd = data.get("capture_device")
        self._cap_device = cd if isinstance(cd, str) and cd else None
        self.metronome = bool(data.get("metronome"))
        self.pitch = _clampi(data.get("pitch"), -12, 12, 0)
        self.master = _clampi(data.get("master"), 0, 100, 80)
        saved = data.get("tracks")
        if isinstance(saved, list) and saved:
            self.tracks = [
                self._norm_track(i, saved[i] if i < len(saved) else None)
                for i in range(TRACKS)]
        else:
            self.tracks = self._default_tracks()
        # transport must never sit past the (possibly shorter) tape end
        self.pos = min(getattr(self, "pos", 0.0), self.length)
        if self.rec_start is not None:
            self.rec_start = min(self.rec_start, self.length)

    def _load_state(self):
        """Restore the last session from sequencer.json (session recovery)."""
        data = None
        try:
            with open(CFG_FILE) as fh:
                data = json.load(fh)
        except Exception:
            data = None
        self._apply(data)

    def _serialize(self):
        """The full project as a plain dict (autosave and File ▸ Save share it)."""
        return {
            "version": 2,
            "bpm": self.bpm,
            "capture_device": self._cap_device,
            "metronome": self.metronome,
            "length": self.length,
            "pitch": self.pitch,
            "master": self.master,
            "tracks": [
                {"name": tk["name"], "input": tk["input"],
                 "armed": tk["armed"], "muted": tk["muted"],
                 "solo": tk["solo"], "gain": tk["gain"], "pan": tk["pan"],
                 "clips": [([s, e, w] if w else [s, e])
                           for (s, e, w) in map(clip_parts, tk["clips"])]}
                for tk in self.tracks
            ],
        }

    def _save_soon(self):
        """Coalesce rapid mutations (a slider drag fires value-changed on every
        frame) into one deferred disk write, so we never serialize + write JSON
        on the GTK main loop per frame. Flushed by _save() / stop / close."""
        if self._save_timer is not None:
            return
        self._save_timer = GLib.timeout_add(600, self._save_timer_fire)

    def _save_timer_fire(self):
        self._save_timer = None
        self._save()
        return False

    def _save(self):
        """Autosave the whole project (immediate). Cancels any pending debounced
        write first, and never lets an I/O error reach the UI (tasks.py)."""
        if self._save_timer is not None:
            try:
                GLib.source_remove(self._save_timer)
            except Exception:
                pass
            self._save_timer = None
        try:
            nbapp.atomic_write_json(CFG_FILE, self._serialize())
        except Exception:
            pass

    # ================= undo / redo =================
    # A take costs the user real time at the microphone, and until now one click
    # on a track's bin — or Clear All Takes, or shortening the tape — threw it
    # away for good. Every one of those is now a step back.
    def _arrangement(self):
        """The part of a project a single click can destroy: the tape length,
        and each track's takes, name, instrument and switches.
        The continuous mix controls (gain, pan, tempo, pitch, master) are
        deliberately NOT in here — they are visible on their own faders and
        putting one back is a drag, not a lost recording. Leaving them out keeps
        Undo from moving a control the user never touched."""
        return {
            "length": self.length,
            # the file the arrangement belongs to travels with it, so undoing a
            # New or an Open cannot leave Save pointed at the wrong project
            "path": self._path,
            "tracks": [
                {"name": tk["name"], "input": tk["input"],
                 "armed": tk["armed"], "muted": tk["muted"], "solo": tk["solo"],
                 "clips": [tuple(c) for c in tk["clips"]]}
                for tk in self.tracks],
        }

    def _restore_arrangement(self, snap):
        for tk, saved in zip(self.tracks, snap["tracks"]):
            tk.update(saved)
            tk["clips"] = list(saved["clips"])   # never alias the snapshot
        self.length = snap["length"]
        self._path = snap["path"]
        self.pos = min(self.pos, self.length)
        if self.rec_start is not None:
            self.rec_start = min(self.rec_start, self.length)

    def _remember(self):
        """Bank the arrangement so the edit about to happen can be undone. Any
        fresh edit makes the redone future unreachable, so the Redo trail goes."""
        self._undo_stack.append(self._arrangement())
        if len(self._undo_stack) > UNDO_DEPTH:
            self._undo_stack.pop(0)
        self._redo_stack = []

    def _step_history(self, take, give):
        """Move one snapshot from `take` to `give` and adopt it. Undo and Redo
        are the same operation with the stacks swapped."""
        # End a take in flight first: it would otherwise be committed on top of
        # the arrangement we are about to put back. That commit banks a frame of
        # its own, which can empty the Redo trail — so re-check afterwards.
        if self.transport == "rec":
            self._stop_transport()
        if not take:
            return False
        give.append(self._arrangement())
        if len(give) > UNDO_DEPTH:
            give.pop(0)
        self._restore_arrangement(take.pop())
        self._sync_controls()       # names / instruments back into the heads
        self._update_length_btn()
        self._update_proj()
        self._save()
        self.refresh()
        return True

    def _undo(self):
        self._step_history(self._undo_stack, self._redo_stack)

    def _redo(self):
        self._step_history(self._redo_stack, self._undo_stack)

    def _on_destroy(self, *_):
        # never leave an arecord/aplay child running past the window
        try:
            self.recorder.stop()
            self.player.stop()
        except Exception:
            pass
        if self._runner_id is not None:
            try:
                GLib.source_remove(self._runner_id)
            except Exception:
                pass
            self._runner_id = None
        if self._saved_timer is not None:
            try:
                GLib.source_remove(self._saved_timer)
            except Exception:
                pass
            self._saved_timer = None
        self._save()
        self.engine.shutdown()
        return False

    # ================= File menu (project files) =================
    def _write_file(self, path):
        """Serialise the project to `path`. Returns True on success."""
        try:
            nbapp.atomic_write_json(path, self._serialize(),
                                    ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False

    def _open_file(self, path):
        """Load a project file, then push it into every control. True on ok."""
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            self._flash("Couldn't open that project")
            return False
        # Every app writes JSON into the shared Documents folder, so a readable
        # dict is not proof this is ours. Verify the Sequencer project shape
        # BEFORE mutating anything — adopting a foreign file would wipe the
        # tracks, overwrite session recovery with the empty default, and let a
        # later Save clobber that file. On mismatch flash and change nothing.
        if not isinstance(data, dict) or not isinstance(data.get("tracks"),
                                                         list):
            self._flash("That file isn't a Sequencer project")
            return False
        if self.transport == "rec":
            self._stop_transport()
        self._remember()   # opening the wrong project is one step back
        self._apply(data)
        self._path = path
        self._sync_controls()
        self._update_proj()
        self._save()            # snapshot recovery adopts the opened project
        return True

    def _file_new(self):
        """Blank project (empty tape, defaults). Confirms first when there are
        recorded takes a new project would discard; the file on disk is left
        alone either way."""
        if any(tk["clips"] for tk in self.tracks):
            self._confirm(
                _t("New project?"),
                _t("This clears the current tracks and takes for a blank "
                   "project. Save first if you want to keep them."),
                _t("New Project"), self._do_file_new)
            return
        self._do_file_new()

    def _do_file_new(self):
        if self.transport == "rec":
            self._stop_transport()
        self._remember()
        self._apply({})
        self._path = None
        self._sync_controls()
        self._update_proj()
        self._save()

    def _file_open(self):
        path = self._choose_file(save=False)
        if not path or not os.path.isfile(path):
            return
        # opening replaces the in-memory project; confirm when live takes would
        # be discarded (a project already written to a file is safe on disk).
        if any(tk["clips"] for tk in self.tracks):
            self._confirm(
                _t("Open this project?"),
                _t("Opening replaces the current tracks and takes. Save first "
                   "if you want to keep them."),
                _t("Open"), lambda: self._open_file(path))
            return
        self._open_file(path)

    def _file_save(self):
        """Write to the current project file; prompt via Save As if none."""
        if not self._path:
            return self._file_save_as()
        if self._write_file(self._path):
            self._update_proj()
            self._flash_saved()
        else:
            self._flash("Couldn't save the project")

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

    def _flash(self, text):
        """Surface a transient file-op error in the project status (crash-safe)."""
        try:
            self.proj_lbl.set_markup(
                '<span foreground="#C8341E">● </span>%s'
                % GLib.markup_escape_text(text))
        except Exception:
            pass

    def _flash_saved(self):
        """Confirm an explicit File ▸ Save with a green-dot 'Saved HH:MM' chip
        (the write model the other File-Save apps share), then restore the plain
        project path after a moment. Session recovery autosaves continuously;
        this reassures the user the named *project file* was actually written."""
        try:
            when = GLib.DateTime.new_now_local().format("%H:%M")
            self.proj_lbl.set_markup(
                '<span foreground="#7FA98C">● </span>Saved %s' % when)
        except Exception:
            return
        if self._saved_timer is not None:
            try:
                GLib.source_remove(self._saved_timer)
            except Exception:
                pass
        self._saved_timer = GLib.timeout_add(1600, self._saved_restore)

    def _saved_restore(self):
        self._saved_timer = None
        self._update_proj()
        return False

    def _update_proj(self):
        """Refresh the project status: the open file's path, or the empty-state
        prompt when no project file is loaded."""
        try:
            if self._path:
                p = self._path
                if p == HOME or p.startswith(HOME + os.sep):
                    p = os.path.join("~", os.path.relpath(p, HOME))
                self.proj_lbl.set_text(p)
            else:
                self.proj_lbl.set_text("No project — File ▸ New / Open")
        except Exception:
            pass

    def _sync_controls(self):
        """Push loaded state into every widget (used by New / Open). Guarded so
        the value-changed handlers don't fight the load or re-fire autosaves."""
        self._loading = True
        try:
            self.pitch_scale.set_value(self.pitch)
            self.master_scale.set_value(self.master)
            self.bpm_scale.set_value(self.bpm)
            for i, tk in enumerate(self.tracks):
                tw = self.track_widgets[i]
                tw["gain"].set_value(tk["gain"])
                tw["pan"].set_value(tk["pan"])
                tw["inst"].set_label(tk["input"])
                tw["name"].set_text(tk["name"])
                # the name ellipsizes in the fixed-width head — keep the full
                # one reachable on hover after a project load
                tw["name"].set_tooltip_text(tk["name"])
        finally:
            self._loading = False
        self._update_length_btn()
        self.refresh()

    # ================= transport bar =================
    def _transport_bar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        bar.get_style_context().add_class("transport")
        # A box child fills its parent's height whatever its own size says, so
        # every control here was being stretched to the whole deck: the METRO
        # toggle and the LENGTH readout came out as 110px-tall slabs instead of
        # the 30px pills they are styled as. Centre the row so each control is
        # the size it asks for.
        bar.set_valign(Gtk.Align.CENTER)

        btns = Gtk.Box(spacing=6)
        self.tbuttons = {}
        specs = [("rew", "rew", self._on_rew), ("ff", "ff", self._on_ff),
                 ("stop", "stopsq", self._on_stop), ("play", "play",
                  self._on_play)]
        for key, icon, cb in specs:
            b = Gtk.Button()
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("tbtn")
            img = Gtk.Image()
            b.add(img)
            b.connect("clicked", cb)
            self.tbuttons[key] = (b, img, icon)
            btns.pack_start(b, False, False, 0)
        # record button with a dot
        rec = Gtk.Button()
        rec.set_relief(Gtk.ReliefStyle.NONE)
        rec.get_style_context().add_class("tbtn")
        rec.get_style_context().add_class("recbtn")
        # the rec dot sits on the rec button, not the rail — its opaque backing
        # tracks the button surface (base #FCFBF8 → signage red when armed)
        self.recdot = Dot(14, bg=SURF)
        self.recdot.set_color(RED)
        rec.add(self.recdot)
        rec.connect("clicked", self._on_rec)
        self.tbuttons["rec"] = (rec, None, None)
        btns.pack_start(rec, False, False, 0)
        bar.pack_start(btns, False, False, 0)

        rtz = Gtk.Button(label=_t("RTZ"))
        rtz.set_relief(Gtk.ReliefStyle.NONE)
        rtz.get_style_context().add_class("rtz")
        # RTZ is deck shorthand; say what it does for anyone who has never used
        # a tape machine
        rtz.set_tooltip_text(_t("Stop and go back to the start"))
        rtz.connect("clicked", lambda *_: self._stop_transport(0))
        bar.pack_start(rtz, False, False, 0)

        self.counter = Gtk.Label(label="00:00.0")
        self.counter.get_style_context().add_class("counter")
        self.counter.set_tooltip_text(
            _t("Position: bar and beat, then minutes:seconds"))
        bar.pack_start(self.counter, False, False, 0)

        stbox = Gtk.Box(spacing=8)
        self.statusdot = Dot(8)
        stbox.pack_start(self.statusdot, False, False, 0)
        self.statuslbl = Gtk.Label(label=_t("Stopped"))
        self.statuslbl.get_style_context().add_class("tstatus")
        stbox.pack_start(self.statuslbl, False, False, 0)
        bar.pack_start(stbox, False, False, 0)

        sep0 = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep0.get_style_context().add_class("vsep")
        bar.pack_start(sep0, False, False, 0)

        # ---- tempo / metronome / length ----
        tbox = Gtk.Box(spacing=8)
        tbox.pack_start(self._caplabel("Tempo"), False, False, 0)
        self.bpm_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, BPM_MIN, BPM_MAX, 1)
        self.bpm_scale.set_draw_value(False)
        self.bpm_scale.set_value(self.bpm)
        self.bpm_scale.set_size_request(96, -1)
        self.bpm_scale.connect("value-changed", self._on_bpm)
        tbox.pack_start(self.bpm_scale, False, False, 0)
        self.bpm_lbl = Gtk.Label(label=_t("120 BPM"))
        self.bpm_lbl.get_style_context().add_class("smallnum")
        self.bpm_lbl.set_size_request(58, -1)
        self.bpm_lbl.set_xalign(0)
        tbox.pack_start(self.bpm_lbl, False, False, 0)
        self.metro_btn = Gtk.Button(label=_t("METRO"))
        self.metro_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.metro_btn.get_style_context().add_class("metrobtn")
        self.metro_btn.set_tooltip_text(_t("Toggle metronome"))
        self.metro_btn.connect("clicked", self._toggle_metro)
        tbox.pack_start(self.metro_btn, False, False, 0)
        self.metro_dot = Dot(11)
        self.metro_dot.set_color(VU_OFF)
        tbox.pack_start(self.metro_dot, False, False, 0)
        self.bar_lbl = Gtk.Label(label=_t("BAR 1·1"))
        self.bar_lbl.get_style_context().add_class("barlbl")
        self.bar_lbl.set_size_request(58, -1)
        self.bar_lbl.set_xalign(0)
        tbox.pack_start(self.bar_lbl, False, False, 0)
        tbox.pack_start(self._caplabel("Length"), False, False, 0)
        len_btn = Gtk.Button()
        len_btn.set_relief(Gtk.ReliefStyle.NONE)
        len_btn.get_style_context().add_class("lenbtn")
        len_btn.set_tooltip_text(
            _t("Length of the arrangement — click to change"))
        lbox = Gtk.Box(spacing=8)
        self.len_btn = Gtk.Label(label="02:00")   # the value; caret sits beside
        lbox.pack_start(self.len_btn, True, True, 0)
        lcar = Gtk.Label(label="▾")
        lcar.get_style_context().add_class("caret")
        lbox.pack_start(lcar, False, False, 0)
        len_btn.add(lbox)
        len_btn.connect("clicked", self._cycle_length)
        tbox.pack_start(len_btn, False, False, 0)
        bar.pack_start(tbox, False, False, 0)

        bar.pack_start(Gtk.Box(), True, True, 0)  # spacer

        # pitch
        pbox = Gtk.Box(spacing=10)
        pbox.pack_start(self._caplabel("Pitch"), False, False, 0)
        self.pitch_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, -12, 12, 1)
        self.pitch_scale.set_draw_value(False)
        self.pitch_scale.set_value(self.pitch)
        self.pitch_scale.set_size_request(104, -1)
        self.pitch_scale.connect("value-changed", self._on_pitch)
        pbox.pack_start(self.pitch_scale, False, False, 0)
        self.pitch_lbl = Gtk.Label(label="0%")
        self.pitch_lbl.get_style_context().add_class("smallnum")
        self.pitch_lbl.set_size_request(42, -1)
        self.pitch_lbl.set_xalign(0)
        pbox.pack_start(self.pitch_lbl, False, False, 0)
        bar.pack_start(pbox, False, False, 0)

        sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep.get_style_context().add_class("vsep")
        bar.pack_start(sep, False, False, 0)

        # master
        mbox = Gtk.Box(spacing=10)
        mbox.pack_start(self._caplabel("Master"), False, False, 0)
        self.master_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.master_scale.set_draw_value(False)
        self.master_scale.set_value(self.master)
        self.master_scale.set_size_request(130, -1)
        self.master_scale.connect("value-changed", self._on_master)
        mbox.pack_start(self.master_scale, False, False, 0)
        # numeric readout (percentage + dB), kept live in refresh() even when
        # the transport is stopped so the fader always shows where it's set
        self.master_lbl = Gtk.Label(label="80%")
        self.master_lbl.get_style_context().add_class("smallnum")
        self.master_lbl.set_size_request(80, -1)
        self.master_lbl.set_xalign(0)
        mbox.pack_start(self.master_lbl, False, False, 0)
        self.master_vu = VU(10)
        mbox.pack_start(self.master_vu, False, False, 0)
        bar.pack_start(mbox, False, False, 0)

        # The deck packs many fixed-width controls, so its natural width is well
        # over a modest native panel (1366/1280): laid out raw, the right-hand
        # pitch/master cluster is pushed clean off the screen edge. Wrap it in a
        # horizontal scroller (vertical never) so a narrow panel SCROLLS to
        # every control instead of clipping it. When the panel is at least as
        # wide as the deck, the viewport stretches the bar to fill it, so the
        # expanding spacer still right-aligns the master fader exactly as before
        # and no scrollbar shows — the wide-panel look is unchanged.
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        try:
            scroller.set_propagate_natural_height(True)
        except AttributeError:
            pass
        scroller.get_style_context().add_class("transportscroll")
        scroller.add(bar)
        return scroller

    def _caplabel(self, text):
        lbl = Gtk.Label(label=text.upper())
        lbl.get_style_context().add_class("caps")
        return lbl

    # ================= track area =================
    def _track_area(self):
        area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # ruler row — pinned above the scrolling stack, so the bar grid (and the
        # click-to-seek it carries) is reachable however short the panel is
        rrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        rrow.get_style_context().add_class("rulerrow")
        rlabel = Gtk.Label(label="%d-TRACK · %s" % (TRACKS, SR_KHZ.upper()))
        rlabel.get_style_context().add_class("rulercap")
        rlabel.set_xalign(0)
        rcell = Gtk.Box()
        rcell.get_style_context().add_class("headcell")
        rcell.set_size_request(262, -1)
        rcell.pack_start(rlabel, True, True, 0)
        rrow.pack_start(rcell, False, False, 0)
        self._ruler_cell = rcell
        # The ruler's head cell must end exactly where a track head ends, or the
        # bar grid sits left of the clips it measures. Both ASK for 262px, but a
        # head's gain/pan row needs 283 and GTK honours the larger — so put them
        # in one size group and the cell adopts the head's REAL width in the
        # first allocation, whatever the font or a translated label does to it.
        self._head_group = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        self._head_group.add_widget(rcell)
        rrow.pack_start(Ruler(self), True, True, 0)
        # ...and a gutter that mirrors the lane scrollbar, so bar 33 on the
        # ruler stays exactly above bar 33 in the lanes once the stack scrolls
        # (without it every lane is a scrollbar narrower than the ruler and the
        # playhead drifts off the grid towards the end of the tape).
        self._ruler_gutter = Gtk.Box()
        rrow.pack_start(self._ruler_gutter, False, False, 0)
        area.pack_start(rrow, False, False, 0)

        # Track rows. Eight fixed-height heads are taller than a laptop panel
        # (8 x ~103px on top of the deck, ruler and status bar), and GTK cannot
        # shrink a window below its minimum, so on a 768-tall screen the lower
        # tracks — and the status bar under them — were simply unreachable.
        # Scroll the stack instead and keep the deck, ruler and status pinned.
        stack = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.lanes = []
        self.track_widgets = []
        for i in range(len(self.tracks)):
            stack.pack_start(self._track_row(i), True, True, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(206)   # never fewer than two whole tracks
        # A drag on a lane seeks the playhead; the ScrolledWindow's own capture
        # -phase pan gesture would swallow that press first (same reason the
        # Illustrator canvas disables it).
        scroll.set_kinetic_scrolling(False)
        scroll.set_capture_button_press(False)
        scroll.add(stack)
        area.pack_start(scroll, True, True, 0)

        vsb = scroll.get_vscrollbar()
        if vsb is not None:
            vsb.connect("size-allocate", self._sync_ruler_gutter)
            vsb.connect("notify::visible", self._sync_ruler_gutter)
        return area

    def _sync_ruler_gutter(self, bar, *_a):
        """Keep the ruler's right-hand gutter the width of the lane scrollbar
        (zero when it is hidden), so ruler and lanes always share one grid."""
        w = bar.get_allocated_width() if bar.get_visible() else 0
        if w != self._ruler_gutter.get_size_request()[0]:
            self._ruler_gutter.set_size_request(w, -1)

    def _track_row(self, i):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        row.get_style_context().add_class("trackrow")

        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        head.get_style_context().add_class("trackhead")
        head.set_size_request(262, -1)
        head.set_hexpand(False)  # stop scale hexpand propagating to the row
        self._head_group.add_widget(head)   # one column with the ruler's cell

        # row 1: name + M / S / REC / clear
        r1 = Gtk.Box(spacing=5)
        name = Gtk.Label(label=self.tracks[i]["name"])
        name.get_style_context().add_class("trackname")
        name.set_xalign(0)
        # A track head is a FIXED 262px column and every lane starts where it
        # ends, so a long name (a project file may carry one) must ellipsize:
        # left to grow, it widens that one head and its lane alone, and the
        # row's clips and playhead slide out of register with the ruler and
        # with every other track.
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(10)
        name.set_tooltip_text(self.tracks[i]["name"])
        r1.pack_start(name, True, True, 0)
        mbtns = Gtk.Box(spacing=4)
        mute = Gtk.Button(label=_t("M"))
        mute.set_relief(Gtk.ReliefStyle.NONE)
        mute.get_style_context().add_class("mbtn")
        mute.set_tooltip_text(_t("Mute"))
        mute.connect("clicked", self._toggle, i, "muted")
        solo = Gtk.Button(label=_t("S"))
        solo.set_relief(Gtk.ReliefStyle.NONE)
        solo.get_style_context().add_class("sbtn")
        solo.set_tooltip_text(_t("Solo"))
        solo.connect("clicked", self._toggle, i, "solo")
        arm = Gtk.Button(label=_t("REC"))
        arm.set_relief(Gtk.ReliefStyle.NONE)
        arm.get_style_context().add_class("armbtn")
        arm.set_tooltip_text(_t("Arm for recording"))
        arm.connect("clicked", self._toggle, i, "armed")
        # per-track clear — wipe just this track's takes so one take can be
        # redone without the destructive menu 'Clear All Takes'
        clr = Gtk.Button()
        clr.set_relief(Gtk.ReliefStyle.NONE)
        clr.get_style_context().add_class("clrbtn")
        clrimg = Gtk.Image()
        try:
            clrimg.set_from_pixbuf(nbicons.pixbuf("trash", 15, "#6E695E"))
        except GLib.Error:
            pass
        clr.add(clrimg)
        clr.set_tooltip_text(_t("Clear this track's takes"))
        clr.connect("clicked", self._clear_track, i)
        mbtns.pack_start(mute, False, False, 0)
        mbtns.pack_start(solo, False, False, 0)
        mbtns.pack_start(arm, False, False, 0)
        mbtns.pack_start(clr, False, False, 0)
        r1.pack_start(mbtns, False, False, 0)
        head.pack_start(r1, False, False, 0)

        # row 2: instrument-voice selector (cycles) + VU
        r2 = Gtk.Box(spacing=6)
        inst = Gtk.Button()
        inst.set_relief(Gtk.ReliefStyle.NONE)
        inst.get_style_context().add_class("instbtn")
        inst.set_tooltip_text(
            _t("Instrument — click to change the sound of this track"))
        # a plain word on a flat button gave no hint that it can be changed;
        # the caret is the same affordance Novel and Academic use for their
        # click-to-cycle style pills
        ibox = Gtk.Box(spacing=8)
        instlbl = Gtk.Label(label=self.tracks[i]["input"])
        ibox.pack_start(instlbl, True, True, 0)
        icar = Gtk.Label(label="▾")
        icar.get_style_context().add_class("caret")
        ibox.pack_start(icar, False, False, 0)
        inst.add(ibox)
        inst.connect("clicked", self._cycle_input, i)
        r2.pack_start(inst, False, False, 0)
        r2.pack_start(Gtk.Box(), True, True, 0)
        vu = VU(8)
        r2.pack_start(vu, False, False, 0)
        head.pack_start(r2, False, False, 0)

        # row 3: gain and pan side by side (kept to one row so eight track
        # heads fit the fixed-height surface; the dB / L·R readouts and the
        # tooltips disambiguate the two faders)
        r3 = Gtk.Box(spacing=6)
        gcap = self._minicap("G")
        gcap.set_size_request(10, -1)
        r3.pack_start(gcap, False, False, 0)
        gain = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        gain.set_draw_value(False)
        gain.set_value(self.tracks[i]["gain"])
        gain.set_hexpand(True)
        gain.set_size_request(60, -1)
        gain.set_tooltip_text(_t("Gain"))
        gain.connect("value-changed", self._on_gain, i)
        r3.pack_start(gain, True, True, 0)
        gainlbl = Gtk.Label()
        gainlbl.get_style_context().add_class("smallnum")
        gainlbl.set_size_request(40, -1)
        gainlbl.set_xalign(1)
        r3.pack_start(gainlbl, False, False, 0)
        pcap = self._minicap("P")
        pcap.set_size_request(10, -1)
        r3.pack_start(pcap, False, False, 0)
        pan = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        pan.set_draw_value(False)
        pan.set_value(self.tracks[i]["pan"])
        pan.set_hexpand(True)
        pan.set_size_request(60, -1)
        pan.set_tooltip_text(_t("Pan"))
        pan.connect("value-changed", self._on_pan, i)
        r3.pack_start(pan, True, True, 0)
        panlbl = Gtk.Label()
        panlbl.get_style_context().add_class("smallnum")
        panlbl.set_size_request(40, -1)
        panlbl.set_xalign(1)
        r3.pack_start(panlbl, False, False, 0)
        head.pack_start(r3, False, False, 0)

        row.pack_start(head, False, False, 0)

        lane = Lane(self, i)
        self.lanes.append(lane)
        row.pack_start(lane, True, True, 0)

        self.track_widgets.append({
            # "inst" is the button's LABEL, not the button: set_label() on a
            # button that carries a custom child replaces that child, which
            # would throw the caret away the first time the voice is cycled
            "head": head, "name": name, "mute": mute, "solo": solo,
            "arm": arm, "inst": instlbl, "vu": vu, "gain": gain, "pan": pan,
            "gainlbl": gainlbl, "panlbl": panlbl, "clr": clr})
        return row

    def _minicap(self, text):
        lbl = Gtk.Label(label=text.upper())
        lbl.get_style_context().add_class("minicap")
        lbl.set_xalign(0)
        lbl.set_size_request(28, -1)
        return lbl

    # ================= status bar =================
    def _status_bar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        bar.get_style_context().add_class("statusbar")
        self.armed_lbl = Gtk.Label(label=_t("No tracks armed"))
        self.armed_lbl.set_xalign(0)
        bar.pack_start(self.armed_lbl, False, False, 0)
        self.takes_lbl = Gtk.Label(label=_t("No takes"))
        self.takes_lbl.set_xalign(0)
        bar.pack_start(self.takes_lbl, False, False, 0)
        # tone-engine state: blank until first Play, then ready / unavailable
        self.audio_lbl = Gtk.Label(label="")
        self.audio_lbl.set_xalign(0)
        bar.pack_start(self.audio_lbl, False, False, 0)
        # right cluster: technical specs then the open project path
        self.specs_lbl = Gtk.Label(label="%s · 16-bit" % SR_KHZ)
        self.specs_lbl.set_xalign(1)
        bar.pack_end(self.specs_lbl, False, False, 0)
        self.proj_lbl = Gtk.Label(label=_t("No project — File ▸ New / Open"))
        self.proj_lbl.set_xalign(1)
        self.proj_lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        bar.pack_end(self.proj_lbl, True, True, 0)
        return bar

    # ================= interaction =================
    def _on_space(self, _w, ev):
        if ev.keyval != Gdk.KEY_space:
            return False
        # A modal overlay (confirm card, About card, or an open menu) owns the
        # keyboard — never drive the transport from behind it. Let the key fall
        # through so it can activate the focused Cancel / OK / menu item.
        if (self._prompt_layer is not None or self._menu_open is not None
                or getattr(self, "_about_layer", None) is not None):
            return False
        self._toggle_play()
        return True

    def _start_audio(self):
        """Bring the tone engine up (lazily, first Play/Record) and re-arm the
        beat tracker so the next runner tick fires a downbeat immediately."""
        self.engine.start()
        self._last_beat = None

    def _ensure_runner(self):
        """Arm the 100ms transport tick if it isn't already running. Called
        whenever the transport engages play/rec/ff/rew; the tick drops itself
        (returns False, clearing this id) once the transport returns to stop,
        so a quiet/stopped window schedules no idle wakeups."""
        if self._runner_id is None:
            self._runner_id = GLib.timeout_add(100, self._runner)

    def _toggle_play(self):
        if self.transport in ("play", "rec"):
            self._stop_transport()
        else:
            self.transport = "play"
            self._start_audio()
            self._ensure_runner()
        self.refresh()

    def _on_play(self, *_):
        # Switching transport mid-record would abandon the in-progress take
        # (rec_start never committed) — ignore Play while recording, matching
        # the lane/ruler click-while-recording guards. Stop/Record ends a take.
        if self.transport == "rec":
            return
        self.transport = "play"
        self._start_audio()
        self._ensure_runner()
        self.refresh()

    def _on_stop(self, *_):
        self._stop_transport()
        self.refresh()

    def _on_rew(self, *_):
        # ignore transport change while recording (see _on_play) so an
        # accidental Rewind can't silently abort the in-progress take
        if self.transport == "rec":
            return
        self.transport = "stop" if self.transport == "rew" else "rew"
        self.engine.silence()   # no step tones while scrubbing
        self._last_beat = None
        if self.transport == "rew":
            self._ensure_runner()
        self.refresh()

    def _on_ff(self, *_):
        # ignore transport change while recording (see _on_play) so an
        # accidental Fast-Forward can't silently abort the in-progress take
        if self.transport == "rec":
            return
        self.transport = "stop" if self.transport == "ff" else "ff"
        self.engine.silence()   # no step tones while scrubbing
        self._last_beat = None
        if self.transport == "ff":
            self._ensure_runner()
        self.refresh()

    def _on_rec(self, *_):
        if self.transport == "rec":
            self._stop_transport()
        else:
            self.transport = "rec"
            self.rec_start = self.pos
            self._start_audio()
            self._start_capture()
            self._ensure_runner()
        self.refresh()

    def _set_capture_device(self, dev):
        self._cap_device = dev
        label = dict(capture_devices()).get(dev, dev)
        self._flash("Input: %s" % label)
        self._save_soon()

    def _mic_armed(self):
        """True when at least one armed track is set to the Mic input."""
        return any(tk["armed"] and tk.get("input") == MIC for tk in self.tracks)

    def _capture_device(self):
        """The ALSA device to record from: the user's choice if it is still
        present, else the first real capture device (a USB mic or interface
        shows up here exactly like the built-in codec)."""
        devs = capture_devices()
        names = [d for d, _l in devs]
        if self._cap_device in names:
            return self._cap_device
        return names[0] if names else "default"

    def _start_capture(self):
        """Begin a take if a Mic track is armed. A failure is reported in the
        status line and leaves the synth recording untouched."""
        if not self._mic_armed():
            return
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(TAKES_DIR, "take-%s.wav" % stamp)
        ok, msg = self.recorder.start(self._capture_device(), path)
        if not ok:
            self._flash(msg)

    def _finish_capture(self):
        """End the take and return its WAV path, or None if nothing was
        captured (no Mic track armed, no recorder, or a dead/busy input)."""
        died = self.recorder.failed_early()
        path = self.recorder.stop()
        if died and path is None:
            self._flash("Nothing was recorded — try another input")
        return path

    def _stop_transport(self, seek_to=None):
        wav = self._finish_capture() if self.transport == "rec" else None
        self.player.stop()
        self._played = set()
        committing = (self.transport == "rec" and self.rec_start is not None
                      and self.pos - self.rec_start > 0.2
                      and any(tk["armed"] for tk in self.tracks))
        if committing:
            self._remember()   # a take that lands is a step Undo can walk back
            for tk in self.tracks:
                if tk["armed"]:
                    # a Mic track's clip carries the captured audio; a synth
                    # track's clip stays a plain region the engine voices.
                    if wav and tk.get("input") == MIC:
                        tk["clips"].append((self.rec_start, self.pos, wav))
                    else:
                        tk["clips"].append((self.rec_start, self.pos))
        self.transport = "stop"
        self.rec_start = None
        self.engine.silence()   # cut any ringing step tones on stop
        self._last_beat = None
        if seek_to is not None:
            self.pos = seek_to
        self._played = set()      # a new pass re-triggers every take
        self._save()
        self.refresh()

    def _toggle(self, _b, i, key):
        if self._loading:
            return
        self.tracks[i][key] = not self.tracks[i][key]
        self._save()
        self.refresh()

    def _cycle_input(self, _b, i):
        """Advance this track's instrument voice to the next in the list."""
        if self._loading:
            return
        cur = self.tracks[i]["input"]
        idx = INPUTS.index(cur) if cur in INPUTS else -1
        nxt = INPUTS[(idx + 1) % len(INPUTS)]
        self.tracks[i]["input"] = nxt
        self.track_widgets[i]["inst"].set_label(nxt)
        self._save()
        self.refresh()

    def _clear_track(self, _b, i):
        """Confirm before deleting just this one track's takes (destructive, no
        undo — mirrors 'Clear All Takes'). Leaves the other tracks alone."""
        n = len(self.tracks[i]["clips"])
        if n == 0:
            return
        self._confirm(
            _t("Clear this track's takes?"),
            _t("Removes all %d recorded take%s from %s. "
               "Undo (Ctrl+Z) puts them back.")
            % (n, "" if n == 1 else "s", self.tracks[i]["name"]),
            _t("Clear Takes"), lambda: self._do_clear_track(i))

    def _do_clear_track(self, i):
        """Wipe one track's takes — runs only after the confirm is accepted."""
        if not self.tracks[i]["clips"]:
            return
        self._remember()
        # if this armed track is mid-take, end recording first so the take
        # being wiped can't be re-committed on the next Stop
        if self.transport == "rec" and self.tracks[i]["armed"]:
            self._stop_transport()
        self.tracks[i]["clips"] = []
        self._save()
        self.refresh()

    def _on_gain(self, scale, i):
        if self._loading:
            return
        self.tracks[i]["gain"] = int(scale.get_value())
        self._save_soon()
        self.refresh()

    def _on_pan(self, scale, i):
        if self._loading:
            return
        self.tracks[i]["pan"] = int(scale.get_value())
        self._save_soon()
        self.refresh()

    def _on_pitch(self, scale):
        if self._loading:
            return
        self.pitch = int(scale.get_value())
        self._save_soon()
        self.refresh()

    def _on_master(self, scale):
        if self._loading:
            return
        self.master = int(scale.get_value())
        self._save_soon()
        self.refresh()

    def _on_bpm(self, scale):
        if self._loading:
            return
        # Keep the arrangement the same number of BARS across a tempo change.
        # The tape is stored in seconds, so leaving it alone would silently
        # change how much music fits: 32 bars at 120 BPM is 64s, but the same
        # 64s is only 21 bars at 40 BPM. Re-derive the seconds from the bars.
        bars = self.bars_total()
        self.bpm = int(scale.get_value())
        self.set_length_bars(bars)
        # A faster tempo makes the same number of bars fewer SECONDS, and every
        # take is stored in seconds — so takes near the end would fall off the
        # tape, vanish from the lanes and become unreachable by the playhead.
        # A tempo change must never cost the user a recording: keep enough bars
        # to still hold the last one.
        last = max((e for tk in self.tracks
                    for (_s, e, _w) in map(clip_parts, tk["clips"])),
                   default=0.0)
        if last > self.length:
            self.set_length_bars(math.ceil(last / self.sec_per_bar()))
        self._update_length_btn()
        self._last_beat = None          # re-lock the beat grid to the new tempo
        self._save_soon()
        self.refresh()

    def _toggle_metro(self, *_):
        if self._loading:
            return
        self.metronome = not self.metronome
        self._save()
        self.refresh()

    # ---- musical time -----------------------------------------------------
    def sec_per_beat(self):
        return 60.0 / max(1, self.bpm)

    def sec_per_bar(self):
        return self.sec_per_beat() * BEATS_PER_BAR

    def bars_total(self):
        """Length of the tape in bars at the current tempo."""
        spb = self.sec_per_bar()
        return max(1, int(round(self.length / spb))) if spb > 0 else 1

    def bar_beat_at(self, t):
        """(bar, beat) for a time in seconds, both 1-based, as a musician
        counts them: bar 1 beat 1 is the downbeat at 0:00."""
        spb = self.sec_per_beat()
        if spb <= 0:
            return 1, 1
        total_beats = int(t / spb + 1e-6)
        return total_beats // BEATS_PER_BAR + 1, total_beats % BEATS_PER_BAR + 1

    # ---- parts sketched straight onto a lane ------------------------------
    # Recording is the honest way to capture a microphone, but it costs the
    # user real time: eight bars at 120 BPM is sixteen seconds of sitting and
    # watching the playhead, per part. Dragging a part onto a lane builds the
    # same arrangement in a second, which is what makes a loop something a
    # beginner can actually try things with.
    def snap_part(self, t0, t1):
        """A dragged region rounded out to whole bars — never shorter than one,
        never past the end of the tape. Parts land on the same grid the ruler
        draws, so an arrangement is in time by construction."""
        spb = self.sec_per_bar()
        if spb <= 0:
            return (0.0, 0.0)
        s = max(0.0, math.floor(t0 / spb + 1e-6) * spb)
        e = min(self.length, math.ceil(t1 / spb - 1e-6) * spb)
        if e - s < spb - 1e-6:
            e = min(self.length, s + spb)
            s = max(0.0, e - spb)
        return (s, e)

    def add_part(self, i, t0, t1):
        """Lay a part down on track `i`. Undoable like every other edit."""
        s, e = self.snap_part(t0, t1)
        if e - s < 0.001:
            return
        self._remember()
        tk = self.tracks[i]
        tk["clips"] = _merge_parts(list(tk["clips"]) + [(s, e)])
        self._save()
        self.refresh()

    def remove_part(self, i, t):
        """Take the part under `t` off track `i` (right-click). Undo restores
        it — and a captured take's WAV is left on disk, so undo gets the sound
        back too, not just the region."""
        tk = self.tracks[i]
        keep = [c for c in tk["clips"]
                if not (clip_parts(c)[0] <= t <= clip_parts(c)[1])]
        if len(keep) == len(tk["clips"]):
            return
        self._remember()
        tk["clips"] = keep
        self._save()
        self.refresh()

    def set_length_bars(self, bars):
        """Set the tape length to a whole number of bars at the current tempo."""
        self.length = max(1, int(bars)) * self.sec_per_bar()

    def _update_length_btn(self):
        # bars first (how the arrangement is actually counted), with the real
        # duration alongside so the tape length is never a mystery.
        self.len_btn.set_label("%d bars \u00b7 %s"
                               % (self.bars_total(), _fmt_len(self.length)))

    def _cycle_length(self, *_):
        """Advance the pattern length to the next step in LEN_CHOICES, wrapping
        back to the shortest after the longest so the control is fully cyclable
        (never stuck at 04:00 with no way to shorten again). If wrapping to a
        shorter length would trim or drop recorded takes past the new end,
        confirm first (destructive, no undo); an empty tape just cycles."""
        cur_bars = self.bars_total()
        nxt_bars = next((b for b in BAR_CHOICES if b > cur_bars), BAR_CHOICES[0])
        nxt = nxt_bars * self.sec_per_bar()
        if nxt < self.length:
            lost = sum(1 for tk in self.tracks
                       for (s, e, _w) in map(clip_parts, tk["clips"])
                       if e > nxt + 0.001)
            if lost:
                self._confirm(
                    _t("Shorten to %s?") % _fmt_len(nxt),
                    _t("Trims or removes %d recorded take%s past the new end. "
                       "Undo (Ctrl+Z) puts them back.")
                    % (lost, "" if lost == 1 else "s"),
                    _t("Shorten"), lambda: self._set_length(nxt))
                return
        self._set_length(nxt)

    def _set_length(self, v):
        """Adopt a new pattern length, clamping takes and the playhead to it."""
        self._remember()   # shortening trims takes — make that a step back
        self.length = _clampf(v, 10.0, 600.0, DEFAULT_LEN)
        for tk in self.tracks:
            trimmed = []
            for (s, e, w) in map(clip_parts, tk["clips"]):
                if s >= self.length - 0.001:
                    continue
                e = min(e, self.length)
                if e - s > 0.001:
                    # carry the take's audio through the trim — rebuilding the
                    # clip as a bare (start, end) dropped the recorded WAV, so
                    # shortening the tape silently turned every mic take that
                    # survived into an empty region
                    trimmed.append((s, e, w) if w else (s, e))
            tk["clips"] = trimmed
        self.pos = min(self.pos, self.length)
        if self.rec_start is not None:
            self.rec_start = min(self.rec_start, self.length)
        self._update_length_btn()
        self._save()
        self.refresh()

    # ================= bulk track ops (menu helpers) =================
    def _arm_all(self, on):
        self._remember()
        for tk in self.tracks:
            tk["armed"] = bool(on)
        self._save()
        self.refresh()

    def _mute_all(self, on):
        self._remember()
        for tk in self.tracks:
            tk["muted"] = bool(on)
        self._save()
        self.refresh()

    def _solo_all(self, on):
        self._remember()
        for tk in self.tracks:
            tk["solo"] = bool(on)
        self._save()
        self.refresh()

    def _clear_takes_confirm(self):
        """Confirm before wiping every recorded take (destructive, no undo)."""
        n = sum(len(tk["clips"]) for tk in self.tracks)
        if n == 0:
            return
        self._confirm(
            _t("Clear all takes?"),
            _t("Removes all %d recorded take%s from every track. "
               "Undo (Ctrl+Z) puts them back.") % (n, "" if n == 1 else "s"),
            _t("Clear All"), self._clear_takes)

    def _clear_takes(self):
        if self.transport == "rec":
            self._stop_transport()
        self._remember()
        for tk in self.tracks:
            tk["clips"] = []
        self._save()
        self.refresh()

    # ================= menus =================
    def menu_items(self, name):
        if name == "File":
            return [
                ("New    Ctrl+N", self._file_new),
                ("Open…    Ctrl+O", self._file_open),
                nbapp.SEP,
                ("Save    Ctrl+S", self._file_save),
                ("Save As…    Ctrl+Shift+S", self._file_save_as),
                nbapp.SEP,
                ("Close    Esc", self.close),
            ]
        if name == "Edit":
            # The base Cut/Copy/Paste act on a focused text widget, of which
            # this app has none — they would be dead. The arrangement history
            # is what an Edit menu means here.
            return [
                ("Undo    Ctrl+Z", self._undo if self._undo_stack else None),
                ("Redo    Ctrl+Y", self._redo if self._redo_stack else None),
            ]
        if name == "Transport":
            # a leading check marks the metronome when it is on, so the menu
            # shows its state (matching the View menus across the DE). The
            # word is translated BEFORE the mark is glued on: the menu builder
            # looks the whole label up, and "    Metronome" matches no catalog
            # key, so a marked item would sit in English among translated ones.
            metro = "✓ " if self.metronome else "    "
            return [
                ("Play    Space", lambda: self._on_play()),
                ("Stop", lambda: self._on_stop()),
                ("Record", lambda: self._on_rec()),
                nbapp.SEP,
                ("Rewind", lambda: self._on_rew()),
                ("Fast Forward", lambda: self._on_ff()),
                nbapp.SEP,
                ("Return to Zero", lambda: self._stop_transport(0)),
                (metro + _t("Metronome"), lambda: self._toggle_metro()),
            ]
        if name == "Track":
            return [
                ("Arm All Tracks", lambda: self._arm_all(True)),
                ("Disarm All Tracks", lambda: self._arm_all(False)),
                nbapp.SEP,
                ("Mute All Tracks", lambda: self._mute_all(True)),
                ("Unmute All Tracks", lambda: self._mute_all(False)),
                ("Clear Solo", lambda: self._solo_all(False)),
                nbapp.SEP,
                ("Clear All Takes", lambda: self._clear_takes_confirm()),
            ]
        if name == "Input":
            # Every ALSA capture device, so a USB mic or audio interface can be
            # picked directly rather than hoping "default" points at it. The
            # list is read fresh each time the menu opens, so a device plugged
            # in mid-session appears without restarting the app.
            devs = capture_devices()
            active = self._capture_device()
            items = []
            for dev, label in devs:
                mark = "\u2713 " if dev == active else "    "
                # translate before the mark is glued on (see Metronome above).
                # A real device's name comes from the hardware and has no
                # catalog key, so it passes through untouched \u2014 but our own
                # "System default input" fallback does get translated.
                items.append((mark + _t(label),
                              lambda d=dev: self._set_capture_device(d)))
            if not items:
                items = [(_t("No microphone or input found"), None)]
            items.append(nbapp.SEP)
            # what the user needs to know, not where the files sit: the raw
            # dotfile path was meaningless to anyone who isn't the person who
            # wrote it (and the folder is hidden anyway)
            items.append(("Recordings are saved with your project", None))
            return items
        return super().menu_items(name)

    # ================= animation =================
    def _play_takes(self):
        """Sound any captured take whose clip the playhead has just entered.

        Each clip fires once per pass (tracked in _played, cleared on stop and
        on seek), and a muted track — or any track while another is soloed —
        stays silent, matching how the synth voices are gated. Gated through
        _audible() so mute/solo mean exactly one thing: this read tk["mute"],
        a key that does not exist (it is "muted"), so muting a track silenced
        its synth voice but its recorded takes kept playing."""
        for tk in self.tracks:
            if not self._audible(tk):
                continue
            for c in tk["clips"]:
                st, en, wav = clip_parts(c)
                if not wav or not (st <= self.pos < en):
                    continue
                key = (id(tk), st, wav)
                if key in self._played:
                    continue
                self._played.add(key)
                self.player.play(wav, seek_s=self.pos - st)
        self.player.reap()

    def _runner(self):
        t = self.transport
        if t in ("play", "rec"):
            # varispeed: pitch (-12..+12) scales the tape speed by ±12% so the
            # playhead and counter visibly speed up / drag (the % label reads
            # this same factor). Range keeps the factor safely above zero.
            self.pos = min(self.length,
                           self.pos + 0.1 * (1 + self.pitch / 100.0))
            self.tick += 1
            self._audio_tick()
            self._play_takes()
            if self.pos >= self.length:
                self._stop_transport(self.length)
                return True
            self.refresh()
        elif t == "ff":
            self.pos = min(self.length, self.pos + 1.5)
            self.tick += 1
            # auto-disengage FF at the tape end so the transport doesn't stay
            # visibly engaged scrubbing past the end
            if self.pos >= self.length:
                self.transport = "stop"
            self.refresh()
        elif t == "rew":
            self.pos = max(0, self.pos - 1.5)
            self.tick += 1
            # auto-disengage Rewind at the tape start (mirror of FF above)
            if self.pos <= 0:
                self.transport = "stop"
            self.refresh()
        else:
            # transport is stopped — drop the tick so an idle window schedules
            # no wakeups. Re-armed by _ensure_runner on the next engage. (A tick
            # that itself stopped the transport above still returns True and is
            # cleaned up here on the following tick.)
            self._runner_id = None
            return False
        return True

    # ================= audio (real tone output) =================
    def _track_freq(self, i):
        """The lane's base pitch (Hz), shifted into the voice's register."""
        base = TRACK_HZ[i % len(TRACK_HZ)]
        return base * _VOICE.get(self.tracks[i]["input"], (1.0, False))[0]

    def _audio_tick(self):
        """Called each runner frame while playing/recording: detect a new beat
        (BPM drives the step timing) and voice its tones once. A no-op when the
        engine never came up — the visual sequencer runs on regardless."""
        if self.bpm <= 0:
            return
        beat_len = 60.0 / self.bpm
        cur = int(self.pos / beat_len + 1e-6)
        if cur == self._last_beat:
            return
        self._last_beat = cur
        self._fire_beat(cur, beat_len)

    def _fire_beat(self, beat, beat_len):
        """Voice one beat: the metronome click plus every audible lane whose
        take (or armed monitor) lands on this step."""
        if not self.engine.available or self.transport not in ("play", "rec"):
            return
        # varispeed: the playhead advances ±12% with pitch, so a tape-beat
        # elapses in beat_len/vari REAL seconds — match tone length + pitch to it
        vari = 1.0 + self.pitch / 100.0
        if vari < 0.1:
            vari = 0.1
        real_beat = beat_len / vari
        step = real_beat * 0.6
        if self.metronome:
            down = (beat % BEATS_PER_BAR) == 0
            self.engine.note(1760.0 if down else 1174.0,
                             min(0.09, real_beat * 0.5),
                             0.5 if down else 0.3, perc=True)
        m = self.master / 100.0
        for i, tk in enumerate(self.tracks):
            if not self._audible(tk):
                continue
            # A Mic track carries REAL audio, not a synth voice: its sound comes
            # from the captured WAV (see _play_takes). Voicing a tone for it too
            # would double the take with a synth note that was never played.
            if tk.get("input") == MIC:
                continue
            if self.transport == "rec" and tk["armed"]:
                pass                       # monitor the armed take as it's cut
            elif self._clip_at(tk, self.pos):
                pass                       # a committed take sounds on this step
            else:
                continue
            perc = _VOICE.get(tk["input"], (1.0, False))[1]
            self.engine.note(self._track_freq(i) * vari, step,
                             (tk["gain"] / 100.0) * m, perc=perc)

    def _update_audio_lbl(self):
        """Keep the status-bar sound note current (neutral when absent).

        Says it in the user's terms: "tone engine" is the name of a part of this
        program, and someone who just pressed Play only needs to know whether
        they will hear anything."""
        try:
            if self.engine.available:
                txt = "Sound ready"
            elif self.engine.failed or not GST_OK:
                txt = "No sound on this computer"
            else:
                txt = ""
            self.audio_lbl.set_text(txt)
        except Exception:
            pass

    def is_empty(self):
        """True while the tape holds no takes at all (the first-run state)."""
        return not any(tk["clips"] for tk in self.tracks)

    def _clip_at(self, tk, t):
        return any(s <= t <= e
                   for (s, e, _w) in map(clip_parts, tk["clips"]))

    def _any_solo(self):
        return any(tk["solo"] for tk in self.tracks)

    def _audible(self, tk):
        """A track sounds when it is not muted and either nothing is soloed or
        it is itself soloed."""
        if tk["muted"]:
            return False
        if self._any_solo() and not tk["solo"]:
            return False
        return True

    def _vu_level(self, tk, i):
        if not self._audible(tk):
            return 0.0
        active = ((self.transport == "rec" and tk["armed"])
                  or (self.transport in ("play", "rec")
                      and self._clip_at(tk, self.pos)))
        if not active:
            return 0.0
        t = self.tick
        wob = (abs(math.sin(t * 0.29 + i * 1.7)) * 0.55
               + abs(math.sin(t * 0.11 + i)) * 0.45)
        return min(1.0, (tk["gain"] / 100) * (0.4 + wob * 0.7))

    # ================= refresh =================
    def refresh(self):
        playing = self.transport in ("play", "rec")
        r = self._rendered
        # transport buttons — the active button (and its icon tint) only changes
        # with the transport MODE, so skip the whole loop on the per-100ms ticks
        # where the mode is unchanged: set_from_pixbuf forces an image
        # invalidation every call even though nbicons.pixbuf is cached.
        if r.get("transport") != self.transport:
            r["transport"] = self.transport
            for key, (btn, img, icon) in self.tbuttons.items():
                active = (self.transport == key)
                ctx = btn.get_style_context()
                if key == "rec":
                    if active:
                        ctx.add_class("on")
                        self.recdot.set_bg(RED)
                        self.recdot.set_color((0.99, 0.98, 0.97))
                    else:
                        ctx.remove_class("on")
                        self.recdot.set_bg(SURF)
                        self.recdot.set_color(RED)
                    continue
                if active:
                    ctx.add_class("on")
                    icon_col = "#F4F2EC"
                else:
                    ctx.remove_class("on")
                    icon_col = "#1A1916"
                # a pixbuf hiccup here must not kill the animation source —
                # keep the old icon on failure.
                try:
                    img.set_from_pixbuf(nbicons.pixbuf(icon, 18, icon_col))
                except GLib.Error:
                    pass
        # counter
        # round to the nearest tenth first so float drift on self.pos
        # (0.9999.. etc) doesn't floor a whole tenth/second low
        total_tenths = round(self.pos * 10)
        secs_total = total_tenths // 10
        tenths = total_tenths % 10
        mins = secs_total // 60
        secs = secs_total % 60
        # Musical position first: a sequencer's "where am I" is bar and beat.
        # The clock time stays beside it for anyone syncing to real seconds.
        bar, beat = self.bar_beat_at(self.pos)
        self.counter.set_text("%03d.%d  %02d:%02d.%d"
                              % (bar, beat, mins, secs, tenths))
        # status
        any_armed = any(tk["armed"] for tk in self.tracks)
        if self.transport == "rec":
            st = "Recording" if any_armed else "Record — no track armed"
            self.statusdot.set_color(RED)
        elif self.transport == "play":
            st = "Playing"
            self.statusdot.set_color(INK)
        elif self.transport == "ff":
            st = "Fast forward"
            self.statusdot.set_color(INK)
        elif self.transport == "rew":
            st = "Rewind"
            self.statusdot.set_color(INK)
        else:
            st = "Stopped"
            self.statusdot.set_color(FAINT)
        self.statuslbl.set_text(st)
        # tempo / metronome / bar readout — BPM only moves when the user drags
        # tempo, so it's off the per-tick path; the bar readout below advances
        # every beat and stays live.
        if r.get("bpm") != self.bpm:
            r["bpm"] = self.bpm
            self.bpm_lbl.set_text("%d BPM" % self.bpm)
        _cls(self.metro_btn, "on", self.metronome)
        beat_len = 60.0 / self.bpm if self.bpm > 0 else 0.0
        beat_on = False
        if beat_len > 0:
            phase = (self.pos % beat_len) / beat_len
            beat_on = phase < 0.5
            total_beats = int(self.pos / beat_len)
            self.bar_lbl.set_text(
                "BAR %d·%d" % (total_beats // BEATS_PER_BAR + 1,
                               total_beats % BEATS_PER_BAR + 1))
        if not self.metronome:
            self.metro_dot.set_color(VU_OFF)
        elif playing and beat_on:
            self.metro_dot.set_color(RED)
        else:
            self.metro_dot.set_color(MUTED)
        # pitch / master readouts — set from their own value, not the transport,
        # so they only rewrite (and the master only recomputes 20·log10) when the
        # user actually moves the fader, never on every 100ms tick.
        if r.get("pitch") != self.pitch:
            r["pitch"] = self.pitch
            self.pitch_lbl.set_text(("+" if self.pitch > 0 else "")
                                    + "%d%%" % self.pitch)
        if r.get("master") != self.master:
            r["master"] = self.master
            mv = self.master
            if mv <= 0:
                self.master_lbl.set_text("0% · off")
            else:
                dbi = int(round(20 * math.log10(mv / 100.0)))
                db_txt = "0" if dbi == 0 else "%+d" % dbi
                self.master_lbl.set_text("%d%% · %s dB" % (mv, db_txt))
        # Master VU: sum the per-track playing levels (same drive as the
        # per-track meters) so the master animates during playback of takes,
        # not just while recording — an empty clips list never went "active".
        master_level = 0.0
        if playing:
            summed = sum(self._vu_level(tk, i)
                         for i, tk in enumerate(self.tracks))
            master_level = min(1.0, summed * (self.master / 100))
        self.master_vu.set_level(master_level)
        # per-track
        for i, tk in enumerate(self.tracks):
            tw = self.track_widgets[i]
            _cls(tw["head"], "armed", tk["armed"])
            _cls(tw["mute"], "on", tk["muted"])
            _cls(tw["solo"], "on", tk["solo"])
            _cls(tw["arm"], "on", tk["armed"])
            g = tk["gain"]
            gdb = int(round((g - 90) / 6))
            tw["gainlbl"].set_text("0 dB" if gdb == 0 else "%+d dB" % gdb)
            p = tk["pan"]
            tw["panlbl"].set_text(
                "C" if p == 50 else
                ("L%d" % ((50 - p) * 2) if p < 50 else "R%d" % ((p - 50) * 2)))
            tw["vu"].set_level(self._vu_level(tk, i))
            # per-track clear is only meaningful once a take exists
            tw["clr"].set_sensitive(bool(tk["clips"]))
        # status bar summaries
        armed = [tk["name"] for tk in self.tracks if tk["armed"]]
        if not armed:
            armed_txt = "No tracks armed"
        elif len(armed) <= 2:
            armed_txt = ", ".join(armed) + " armed"
        else:
            armed_txt = "%d tracks armed" % len(armed)
        self.armed_lbl.set_text(armed_txt)
        n = sum(len(tk["clips"]) for tk in self.tracks)
        self.takes_lbl.set_text(
            "No takes" if n == 0 else "%d take%s" % (n, "" if n == 1 else "s"))
        self.specs_lbl.set_text(
            "%s · 16-bit · %d tracks · %s"
            % (SR_KHZ, TRACKS, _fmt_len(self.length)))
        # tone-engine status note
        self._update_audio_lbl()
        # lanes — sync() repaints a lane in full only when its static content
        # (clips / mute / solo / length / arm / size) changed; otherwise it just
        # invalidates the thin strip the playhead sweeps, so the running
        # transport never re-rasterises all eight full-width lanes per tick.
        for lane in self.lanes:
            lane.sync()

    # ================= confirmation card =================
    def _confirm(self, title, message, ok_label, on_ok):
        """A small in-window confirm card for a destructive action (shares the
        overlay idiom used across the DE — no popup window on this no-compositor
        stack). `on_ok` runs only on accept; Cancel / scrim / Esc dismiss it and
        change nothing."""
        self._close_prompt()
        alloc = self.get_allocation()
        # Size the scrim + centre the card off the LIVE window allocation,
        # falling back to the real primary-monitor size (nbapp.screen_size) —
        # NEVER a hardcoded 1920x1080. On a smaller native panel the old
        # max(alloc, 1920/1080) overflowed the scrim and dropped the card
        # off-centre / off-screen; sizing to the actual panel keeps it centred.
        _sw, _sh = nbapp.screen_size()
        W = alloc.width if alloc.width > 1 else _sw
        H = alloc.height if alloc.height > 1 else _sh
        layer = Gtk.Fixed()
        scrim = Gtk.EventBox()
        scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        scrim.set_size_request(W, H)
        # tint it: an untinted scrim caught the clicks but showed nothing, so a
        # destructive confirm read as a card floating over a live, still-usable
        # window rather than as the modal question it is
        scrim.get_style_context().add_class("seqscrim")
        scrim.connect("button-press-event",
                      lambda *a: (self._close_prompt(), True)[1])
        layer.put(scrim, 0, 0)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        card.get_style_context().add_class("seqprompt")
        head = Gtk.Label(label=title, xalign=0)
        head.get_style_context().add_class("seqprompttitle")
        card.pack_start(head, False, False, 0)
        msg = Gtk.Label(label=message, xalign=0)
        msg.get_style_context().add_class("seqpromptmsg")
        msg.set_line_wrap(True)
        msg.set_max_width_chars(38)
        card.pack_start(msg, False, False, 0)

        def _accept(*_a):
            self._close_prompt()
            on_ok()

        btnrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btnrow.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label=_t("Cancel"))
        cancel.set_relief(Gtk.ReliefStyle.NONE)
        cancel.get_style_context().add_class("seqpromptcancel")
        cancel.connect("clicked", lambda *_: self._close_prompt())
        ok = Gtk.Button(label=ok_label)
        ok.set_relief(Gtk.ReliefStyle.NONE)
        ok.get_style_context().add_class("seqpromptok")
        ok.connect("clicked", _accept)
        btnrow.pack_start(cancel, False, False, 0)
        btnrow.pack_start(ok, False, False, 0)
        card.pack_start(btnrow, False, False, 0)

        card_win = Gtk.EventBox()   # own GdkWindow so it blits (see nbapp)
        card_win.add(card)
        layer.put(card_win, 0, 0)
        self._overlay.add_overlay(layer)
        layer.show_all()
        _min, nat = card_win.get_preferred_size()
        cw = nat.width if nat.width > 1 else 420
        ch = nat.height if nat.height > 1 else 160
        layer.move(card_win, max((W - cw) // 2, 0), max((H - ch) // 2, 0))
        self._prompt_layer = layer

    def _close_prompt(self, *_):
        """Dismiss the confirm card if one is open. True if it was showing."""
        if self._prompt_layer is not None:
            try:
                self._overlay.remove(self._prompt_layer)
            except Exception:
                pass
            self._prompt_layer = None
            return True
        return False

    def _on_key(self, w, ev):
        # A confirm card takes Esc first; otherwise fall through to the base
        # (which dismisses the About card / an open menu, then quits).
        if ev.keyval == Gdk.KEY_Escape and self._close_prompt():
            return True
        # Project shortcuts, matching the other File-Save apps (Writer /
        # Screenplay). Suppressed while a confirm card is open so a stray
        # Ctrl+N can't stack a second prompt over the first.
        if (ev.state & Gdk.ModifierType.CONTROL_MASK
                and self._prompt_layer is None):
            shift = bool(ev.state & Gdk.ModifierType.SHIFT_MASK)
            kv = ev.keyval
            if kv in (Gdk.KEY_z, Gdk.KEY_Z):
                self._redo() if shift else self._undo()
                return True
            if kv in (Gdk.KEY_y, Gdk.KEY_Y):
                self._redo()
                return True
            if kv in (Gdk.KEY_s, Gdk.KEY_S):
                self._file_save_as() if shift else self._file_save()
                return True
            if kv in (Gdk.KEY_o, Gdk.KEY_O) and not shift:
                self._file_open()
                return True
            if kv in (Gdk.KEY_n, Gdk.KEY_N) and not shift:
                self._file_new()
                return True
        return super()._on_key(w, ev)

    # ================= css =================
    def _install_css(self):
        css = b"""
        /* ---- transport deck ---- */
        .transport { background: #F1EEE6; border-bottom: 1px solid #C9C4B6;
                     min-height: 76px; padding: 0 24px; }
        .transport * { font-family: "Nimbus Sans","Helvetica",sans-serif;
                       color: #1A1916; }
        /* the deck's horizontal scroller: no frame, papertone surface so any
           sliver beside the scrollbar reads as the deck, never a white edge */
        .transportscroll, .transportscroll viewport { background: #F1EEE6;
                       border: none; box-shadow: none; }
        .tbtn { min-width: 52px; min-height: 42px; padding: 0;
                border: 1px solid #C9C4B6; border-radius: 2px;
                background: #FCFBF8; box-shadow: none; }
        .tbtn:hover { background: #EFEBE0; }
        .tbtn.on { background: #1A1916; border-color: #1A1916; }
        .recbtn { color: #C8341E; }
        .recbtn:hover { background: #FBEFEC; }
        .recbtn.on { background: #C8341E; border-color: #C8341E; }
        .rtz { min-height: 34px; padding: 0 14px; border: 1px solid #C9C4B6;
               background: #FCFBF8; border-radius: 2px; font-size: 11px;
               font-weight: 700; letter-spacing: 0.06em; box-shadow: none;
               color: #1A1916; }
        .rtz:hover { background: #EFEBE0; }
        .counter { border: 1px solid #C9C4B6; background: #FCFBF8;
                   color: #1A1916; padding: 6px 16px; border-radius: 2px;
                   font-size: 22px; font-weight: 600; letter-spacing: 0.02em; }
        .tstatus { font-size: 12px; color: #6E695E; }
        .caps { font-size: 11px; color: #6E695E; font-weight: 700;
                letter-spacing: 0.14em; }
        .smallnum { font-size: 12px; color: #6E695E; }
        .barlbl { font-size: 11px; color: #6E695E; font-weight: 700;
                  letter-spacing: 0.08em; }
        .vsep { color: #D7D2C5; margin: 12px 0; }
        .metrobtn { min-height: 30px; padding: 0 12px; border: 1px solid #C9C4B6;
                    background: #FCFBF8; border-radius: 2px; font-size: 10px;
                    font-weight: 700; letter-spacing: 0.08em; box-shadow: none;
                    color: #6E695E; }
        .metrobtn:hover { background: #EFEBE0; }
        .metrobtn.on { background: #1A1916; border-color: #1A1916;
                       color: #FCFBF8; }
        .lenbtn { min-height: 30px; min-width: 58px; padding: 0 12px;
                  border: 1px solid #C9C4B6; background: #FCFBF8;
                  border-radius: 2px; font-size: 13px; font-weight: 600;
                  box-shadow: none; color: #1A1916; }
        .lenbtn:hover { background: #EFEBE0; }

        /* ---- ruler ---- */
        .rulerrow { background: #F1EEE6; border-bottom: 1px solid #C9C4B6;
                    min-height: 30px; }
        .headcell { border-right: 1px solid #C9C4B6; padding: 0 14px; }
        .rulercap { font-size: 10px; color: #9A9484; font-weight: 700;
                    letter-spacing: 0.14em; }

        /* ---- track head ---- */
        .trackrow { border-bottom: 1px solid #D7D2C5; }
        .trackhead { background: #F1EEE6; border-right: 1px solid #C9C4B6;
                     padding: 8px 16px; }
        .trackhead.armed { box-shadow: inset 3px 0 0 #C8341E; }
        .trackhead * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .trackname { font-size: 14px; font-weight: 700; color: #1A1916; }
        .mbtn, .sbtn, .armbtn { min-height: 22px; min-width: 22px;
                         padding: 2px 8px; font-size: 10px;
                         font-weight: 700; letter-spacing: 0.04em;
                         border: 1px solid #C9C4B6; background: #FCFBF8;
                         color: #6E695E; border-radius: 2px; box-shadow: none; }
        .mbtn:hover, .sbtn:hover, .armbtn:hover { background: #EFEBE0; }
        .mbtn.on { background: #1A1916; border-color: #1A1916; color: #FCFBF8; }
        .sbtn.on { background: #C8341E; border-color: #C8341E; color: #FCFBF8; }
        .armbtn { color: #6E695E; }
        .armbtn.on { background: #C8341E; border-color: #C8341E;
                     color: #FCFBF8; }
        .clrbtn { min-height: 22px; min-width: 24px; padding: 1px 4px;
                  border: 1px solid #C9C4B6; background: #FCFBF8;
                  border-radius: 2px; box-shadow: none; }
        .clrbtn:hover { background: #FBEFEC; }
        .clrbtn:disabled { opacity: 0.35; }
        .instbtn { min-height: 22px; min-width: 76px; padding: 2px 12px;
                   font-size: 11px; font-weight: 600; background: #FCFBF8;
                   color: #1A1916; border: 1px solid #C9C4B6;
                   border-radius: 2px; box-shadow: none; }
        .instbtn:hover { background: #EFEBE0; }
        /* the click-to-change caret, quieter than the value it sits beside */
        .caret { font-size: 10px; color: #9A9484; }
        .minicap { font-size: 10px; color: #9A9484; font-weight: 700;
                   letter-spacing: 0.12em; }

        /* ---- sliders ---- */
        scale { padding: 0; margin: 0; }
        scale trough { min-height: 4px; background: #D7D2C5;
                       border: none; border-radius: 2px; }
        scale highlight { background: #1A1916; border-radius: 2px; }
        scale slider { min-width: 13px; min-height: 13px; margin: -6px;
                       background: #1A1916; border: none; border-radius: 50%;
                       box-shadow: none; }

        /* ---- status bar ---- */
        .statusbar { background: #F1EEE6; border-top: 1px solid #D7D2C5;
                     min-height: 32px; padding: 0 18px; }
        .statusbar label { font-size: 12px; color: #6E695E; }

        /* ---- confirm card (destructive-action prompt) ---- */
        .seqscrim { background: rgba(26,25,22,0.28); }
        /* min-width sets the card's measure: a wrapping label's natural width
           is computed from the font's AVERAGE character, which for this face
           is far narrower than the real text, so the body was breaking into a
           ragged four-line column half the width of its own title. */
        .seqprompt { background: #F8F7F2; border: 1px solid #C4BFB1;
                     padding: 22px 26px; min-width: 330px; }
        .seqprompt * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .seqprompttitle { font-size: 17px; font-weight: 700; color: #1A1916; }
        .seqpromptmsg { font-size: 13px; color: #6E695E; }
        .seqpromptcancel { min-height: 30px; padding: 0 16px;
                           border: 1px solid #C9C4B6; background: #FCFBF8;
                           border-radius: 2px; font-size: 13px; color: #1A1916;
                           box-shadow: none; }
        .seqpromptcancel:hover { background: #EFEBE0; }
        .seqpromptok { min-height: 30px; padding: 0 16px;
                       border: 1px solid #C8341E; background: #C8341E;
                       border-radius: 2px; font-size: 13px; font-weight: 700;
                       color: #FCFBF8; box-shadow: none; }
        .seqpromptok:hover { background: #B12D19; }

        /* The system theme sets `* { color: ink }`, which matches a button's
           LABEL node directly, so a colour set on the button itself never
           reaches its text. Every reversed (dark/red) control therefore has to
           name the label too, or it renders ink-on-ink: an engaged METRO and a
           muted track's M were solid unreadable slabs, and the confirm card's
           primary button lost its white text. */
        .metrobtn.on label, .mbtn.on label, .sbtn.on label,
        .armbtn.on label, .seqpromptok label { color: #FCFBF8; }
        """
        prov = Gtk.CssProvider()
        prov.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), prov,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)


def _cls(widget, name, on):
    ctx = widget.get_style_context()
    if on:
        ctx.add_class(name)
    else:
        ctx.remove_class(name)


def _merge_parts(clips):
    """Fold overlapping or touching sketched parts into one run, so dragging
    over a lane twice tidies up instead of stacking duplicates nobody can see
    or remove separately. A captured take carries a WAV and is never merged —
    its audio belongs to exactly the region it was recorded over."""
    plain, captured = [], []
    for c in clips:
        s, e, w = clip_parts(c)
        if w:
            captured.append((s, e, w))
        else:
            plain.append((s, e))
    plain.sort()
    out = []
    for s, e in plain:
        if out and s <= out[-1][1] + 1e-6:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out + captured


def _clampi(v, lo, hi, default):
    """int(v) clamped to [lo, hi]; the default on anything non-numeric."""
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def _clampf(v, lo, hi, default):
    """float(v) clamped to [lo, hi]; the default on anything non-numeric."""
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def _fmt_len(v):
    v = int(round(v))
    return "%02d:%02d" % (v // 60, v % 60)


if __name__ == "__main__":
    nbapp.run(Sequencer)
