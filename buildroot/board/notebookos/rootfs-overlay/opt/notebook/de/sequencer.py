#!/usr/bin/env python3
"""
Sequencer — Notebook OS multitrack recorder (native GTK).

Eight identical audio tracks. Every sound in a project was played into a
microphone or an interface and recorded here; nothing in this program makes a
sound of its own except the metronome and the count-in. Three views over one
arrangement, switched from the bar under the deck:

  ARRANGE  a transport deck (rewind / fast-forward / stop / play / record) above
           eight timeline lanes carrying the takes. The lanes ZOOM, from the
           whole song across the window down to a few hundredths of a second, so
           an edit can be placed by eye. A clip can be dragged along its lane or
           onto another one, trimmed by either end, cut in two with the CUT
           tool, repeated, and nudged from the keyboard — all of it either on
           the bar/beat grid or exactly where the pointer is, which is what the
           SNAP control decides.
  EDIT     one take, whole: the part the clip plays drawn solid, everything
           trimmed off it still there and faint, with its level, its fades and
           its two trim handles.
  MIX      a channel strip per track (level, pan, low/high cut, compression,
           reverb and echo sends, mute/solo) plus the master: room size, echo
           time and feedback, tape wobble and the master fader.

Sound comes from nbsynth, which renders the arrangement to stereo PCM. The same
renderer writes File ▸ Export as Audio…, so the file is exactly what was heard.
Recording goes through `arecord` into a pump thread that writes the take's WAV,
meters the input and — with MONITOR on — plays it back through the speakers as
it arrives, so what is being recorded can be heard while it is recorded.

Projects (tracks + mix + takes) are read/written as JSON via the File menu under
$NB_HOME/Documents. A rolling autosave to CFG_DIR/sequencer.json provides
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
# ValueError). Nothing here touches Gst at import time — AudioOut.start()
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
import copy
import queue
import shutil
import subprocess
import sys
import threading
import time
import wave

import nbapp
import nbpicker
import nbicons
import nbmotion
import nbsynth
import nbtransitions
from nbi18n import _t  # noqa: E402

TRACKS = 8            # fixed track count
# How many arrangement steps Undo can walk back. A frame is a small dict of
# numbers and names (see _arrangement), so the history costs nothing worth
# measuring even at this depth.
UNDO_DEPTH = 40
# 32 bars at the default 120 BPM. A song is longer than that and the LENGTH
# control goes to 128 bars, but a two-minute ruler on an empty first run draws
# sixty bars across the window, and a four-bar clip on it is a smear.
DEFAULT_LEN = 64.0    # default arrangement length in seconds
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
# Fader range. 100 is UNITY — the level the take was recorded at — and the
# scale runs past it so a quiet recording can be brought UP. It used to stop at
# 100 mapping to 1.0, which made unity the ceiling; with the old 80 defaults on
# both the track and the master a take played at 0.64 and there was no way to
# raise it. GAIN_CEILING is what the combined track x master is clamped to.
GAIN_UNITY = 100
GAIN_MAX = 150            # +3.5 dB at the top of a single fader
# A CLIP'S OWN LEVEL GOES FURTHER THAN A FADER'S. A fader trims a balance that
# is already roughly right, so half a stop either way is the whole job; a take
# arrives at whatever gain the microphone happened to be set to, and a quiet
# one needs real make-up before it can be balanced against anything at all.
# 400 is +12 dB and is the engine's own ceiling for a clip (normalize_song),
# so the control and the renderer agree about the loudest a take can be.
CLIP_GAIN_MAX = 400
GAIN_CEILING = 2.5        # track x master, so 150 x 150 is not clipped flat
# Capture format: 48 kHz matches the synth engine's SR, 16-bit mono is what a
# single mic or interface input actually provides and keeps the files small.
CAP_RATE = 48000
CAP_FMT = "S16_LE"
# How much of the input the monitor path keeps in flight, in microseconds. A
# monitor is only worth having if what comes back is close enough to what is
# being played that it can be played ALONG to, so these are far shorter than
# aplay's own defaults (half a second, which is a beat and a half at 120 and
# completely unusable). Short enough to play to, long enough to survive the
# render thread taking the CPU for a block on a software-rendered laptop.
MON_BUFFER_US = 60000
MON_PERIOD_US = 15000

# ---- engine ----
SR = nbsynth.SR       # 48 kHz stereo, the rate everything is rendered at
SR_KHZ = "%d kHz" % (SR // 1000)                     # honest rate label (UI)
# How far ahead of the playhead the render thread works. Rendering is faster
# than real time on ordinary hardware but not by a fixed margin — a bar where
# every voice speaks at once costs more than a bar of one held pad note — so
# the queue absorbs the difference. A second is long enough to ride out any
# single bar and short enough that a mute or a fader is heard promptly.
LOOKAHEAD_BLOCKS = 96                                # x 512 frames ~= 1.0 s
# pan / send / tone ranges, stored per track as -100..100 and 0..100
PAN_MAX = 100
SEND_MAX = 100
# Fade lengths the clip editor cycles through, in seconds. A few milliseconds
# is a de-click; a second is a musical fade.
FADE_CHOICES = (0.0, 0.005, 0.05, 0.25, 0.5, 1.0, 2.0)

# ---- the grid, and whether anything has to land on it ----------------------
# THE GRID IS IN BEATS, and FREE is a first-class setting rather than the
# absence of one. Everything that places a moment — moving a clip, trimming an
# end, cutting one in two, dragging a loop out, nudging from the keyboard —
# asks snap_time() where it may land, so the one control below governs all of
# them at once and there is never a gesture that snaps when its neighbour does
# not. FREE is what an audio editor needs and a step sequencer does not: a
# breath before a word, or the exact zero crossing before a snare, is not on
# any grid, and rounding a cut to the nearest sixteenth is how it becomes
# audible.
SNAP_FREE = 0.0
SNAP_CHOICES = ((4.0, "BAR"), (2.0, "1/2"), (1.0, "BEAT"), (0.5, "1/8"),
                (0.25, "1/16"), (SNAP_FREE, "FREE"))
DEFAULT_SNAP = 4.0                                   # bars, how a song is read

# ---- how far into the tape the lanes are looking ---------------------------
# Zoom is a MULTIPLE of "the whole arrangement fits the width of a lane", so 1
# always means the tape end-to-end whatever its length is and there is one
# obvious place to get back to (FIT). The top of the range puts about a fifth
# of a second across a 760px lane, which is close enough to see one cycle of a
# bass note — the point at which "cut it exactly there" stops being a wish.
ZOOM_FIT = 1.0
ZOOM_MAX = 400.0
ZOOM_STEP = 1.6                    # one press of + / one notch of the wheel
# Where the playhead is parked when a running transport scrolls the view to
# catch up with it: far enough in that what has just gone past is still on
# screen, far enough from the right edge that a whole page follows.
FOLLOW_AT = 0.18
# What the tools are. There are two, and the second one is a knife.
TOOL_SELECT = "select"
TOOL_CUT = "cut"

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

# Time is impressed onto the warm tape as quiet ink, in three deliberately
# separate strengths.  Keeping these as ink alpha (rather than three unrelated
# greys) makes the hierarchy survive on both the live SURF and muted RAIL stock.
GRID_BAR_ALPHA = 0.24
GRID_BEAT_ALPHA = 0.12
GRID_SUB_ALPHA = 0.04


def clip_make(s, e, wav=None, off=0.0, gain=1.0, fin=0.005, fout=0.005):
    """Build a clip.

    A clip is one take on one lane: where it starts and ends in the
    arrangement, the WAV it plays, and HOW FAR INTO THAT WAV it begins. That
    last number is what lets a take be trimmed at either end, cut in two and
    repeated across the song without its audio ever being copied — every piece
    is the same file, read from a different place.

    A take also carries its own level and its own fades. They belong to the
    CLIP and not to the track because that is how takes differ: one verse was
    sung closer to the microphone than the next, and the fix for that is on the
    clip, not on the fader that both of them go through."""
    return {"s": float(s), "e": float(e), "wav": wav or None,
            "off": float(off or 0.0), "gain": float(gain),
            "fin": float(fin), "fout": float(fout)}


def clip_norm(c):
    """Coerce anything a project file might hold into a clip dict.

    Projects written before a clip was a dict stored it as the plain tuple
    (start, end), (start, end, wav) or (start, end, wav, offset), and ones
    written while this app had a drum machine carry a "notes" list that no
    longer plays. Those files must keep opening — and keep their takes — so
    every one of those shapes is read here and nowhere else."""
    if isinstance(c, dict):
        try:
            s = float(c.get("s", 0.0))
            e = float(c.get("e", 0.0))
        except (TypeError, ValueError):
            return None
        wav = c.get("wav")
        return {"s": s, "e": e, "wav": wav if isinstance(wav, str) else None,
                "off": _clampf(c.get("off"), 0.0, 3600.0, 0.0),
                "gain": _clampf(c.get("gain"), 0.0, 4.0, 1.0),
                "fin": _clampf(c.get("fin"), 0.0, 30.0, 0.005),
                "fout": _clampf(c.get("fout"), 0.0, 30.0, 0.005)}
    try:
        s, e = float(c[0]), float(c[1])
    except (TypeError, ValueError, IndexError):
        return None
    wav = c[2] if len(c) >= 3 and isinstance(c[2], str) else None
    try:
        off = float(c[3]) if len(c) >= 4 else 0.0
    except (TypeError, ValueError):
        off = 0.0
    return {"s": s, "e": e, "wav": wav, "off": off, "gain": 1.0,
            "fin": 0.005, "fout": 0.005}


def clip_parts(c):
    """(start, end, wav) — the three things every reader of a clip needs."""
    try:
        return float(c["s"]), float(c["e"]), (c.get("wav") or None)
    except Exception:
        return 0.0, 0.0, None


def clip_offset(c):
    """How far into its WAV a clip starts, in seconds. Only a take has one."""
    try:
        return float(c.get("off") or 0.0)
    except Exception:
        return 0.0


def clip_copy(c):
    """A clip that shares nothing with the original.

    Undo banks whole arrangements, and a snapshot that shared a dict with the
    live clip would be edited along with it: stepping back would then restore
    the edit it was supposed to undo."""
    return {"s": c["s"], "e": c["e"], "wav": c.get("wav"),
            "off": c.get("off", 0.0), "gain": c.get("gain", 1.0),
            "fin": c.get("fin", 0.005), "fout": c.get("fout", 0.005)}


def _have(cmd):
    return shutil.which(cmd) is not None


def capture_devices():
    """Every ALSA CAPTURE device, as [(alsa_name, label), ...].

    Parsed from `arecord -l`, whose lines look like

        card 1: Device [USB Audio Device], device 0: USB Audio [USB Audio]

    so a USB microphone or a USB audio interface shows up exactly like the
    built-in codec does — the kernel has CONFIG_SND_USB_AUDIO=y, so any
    USB-audio-class device enumerates as its own card and needs no extra
    driver. The returned name is "plughw:<card>,<device>", which addresses that
    input directly rather than whatever ALSA happens to call "default".

    "plughw", NOT "hw", AND THIS IS NOT COSMETIC. A raw "hw:" name hands the
    application the hardware's own format, take it or leave it, and a capture
    PCM is almost never willing to give one channel: measured on real hardware,
    a HyperX USB microphone and an Intel HDA analog input BOTH publish
    "CHANNELS: 2" and nothing else. The take is recorded as mono (one channel is
    what a microphone lane is), so every one of them answered

        arecord: set_params: Channels count non available

    and exited at once. Recording was therefore impossible on ordinary hardware:
    the take simply never appeared. "plughw" is the same device with alsa-lib's
    conversion layer in front of it, which is what turns the request into
    something the card accepts — the identical reason nbaudio's asound.conf puts
    `plug` in front of the playback and capture slaves it writes.

    Returns an EMPTY list on a machine with no capture hardware, so the Input
    menu can honestly say there is no microphone instead of ticking a "system
    default" that can never record. Never raises: a device that appears
    mid-session is picked up the next time the menu opens."""
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
                devs.append(("plughw:%s,%s" % (card, dev), label))
            if not devs and any(ln.strip().startswith("card ")
                                for ln in out.splitlines()):
                # the tool listed hardware in a shape this parser didn't know:
                # fall back to the system default rather than declare a
                # machine that plainly has an input to be deaf
                devs.append(("default", "System default input"))
        except Exception:
            pass
    # No unconditional "System default input": a machine with no capture
    # hardware used to show a ticked input that could never record, and
    # "No microphone or input found" could never be reached.
    return devs


class StackHistory:
    """Presents this app's own two-stack history to nbapp.undo_menu_items.

    The Sequencer banks whole-arrangement snapshots rather than the text
    checkpoints nbapp.UndoHistory takes, so it cannot use that class directly -
    but its Edit menu must still be worded exactly as Novel, Journal, Academics
    and Illustrator word theirs, naming what a step would take back ("Undo
    Clear All Takes"). This is the six methods undo_menu_items asks for, over
    the stacks the app already keeps. A snapshot banked without a name gives
    the bare "Undo", which is right for drawing a part in a lane."""

    def __init__(self, app):
        # the app, not its lists: New / Open REPLACE the stacks
        self._app = app

    def can_undo(self):
        return bool(self._app._undo_stack)

    def can_redo(self):
        return bool(self._app._redo_stack)

    def undo(self):
        return self._app._undo()

    def redo(self):
        return self._app._redo()

    @staticmethod
    def _top(names):
        # translated here, exactly as nbapp.UndoHistory._label_at does
        if not names or not names[-1]:
            return None
        # translate, THEN trim the ellipsis, exactly as UndoHistory does
        return _t(names[-1]).rstrip(" \u2026")

    def undo_label(self):
        return self._top(self._app._undo_names)

    def redo_label(self):
        return self._top(self._app._redo_names)


def wav_peak(path):
    """Loudest sample in a 16-bit WAV, 0..32767, or -1 if it cannot be read.

    Used to tell a real take from one that is a valid file full of silence —
    the shape a muted input produces, which passes every other check.
    Reads the file itself rather than shelling out: the stdlib wave module is
    already here and a take is a few seconds long."""
    # array, not audioop: audioop was dropped in Python 3.13, and a helper that
    # works on the guest's 3.11 but raises on a newer host is a check that
    # silently stops checking.
    try:
        import wave
        with wave.open(path, "rb") as w:
            if w.getsampwidth() != 2:
                return -1
            frames = w.readframes(min(w.getnframes(), CAP_RATE * 30))
        if not frames:
            return 0
        a = array.array("h")
        a.frombytes(frames[:len(frames) - (len(frames) % 2)])
        if sys.byteorder == "big":
            a.byteswap()               # WAV samples are little-endian
        return max(max(a), -min(a)) if a else 0
    except Exception:                   # noqa: BLE001
        return -1


def clip_peak(c):
    """The loudest sample in the part of its take a clip actually plays,
    0.0..1.0, or -1.0 if the file cannot be read.

    THE CLIP'S WINDOW, NOT THE WHOLE FILE. A take is trimmed before it is
    balanced, and the take a clip was cut out of may hold a count-in, a cough
    and the loudest chorus of the song — none of which is going to be heard.
    Measuring the file would set the level from audio the arrangement never
    plays, which is exactly the mistake a level control is there to fix."""
    wav = (c or {}).get("wav")
    if not wav:
        return -1.0
    try:
        with wave.open(wav, "rb") as w:
            if w.getsampwidth() != 2:
                return -1.0
            rate = w.getframerate() or CAP_RATE
            ch = max(1, w.getnchannels())
            start = int(max(0.0, float(c.get("off") or 0.0)) * rate)
            want = int(max(0.0, float(c["e"]) - float(c["s"])) * rate)
            if start:
                w.setpos(min(start, w.getnframes()))
            frames = w.readframes(min(want or w.getnframes(), w.getnframes()))
        if not frames:
            return 0.0
        a = array.array("h")
        a.frombytes(frames[:len(frames) - (len(frames) % 2)])
        if sys.byteorder == "big":
            a.byteswap()               # WAV samples are little-endian
        if ch > 1:
            a = a[::ch]
        if not a:
            return 0.0
        return max(max(a), -min(a)) / 32768.0
    except Exception:                   # noqa: BLE001
        return -1.0


_WAVE_CACHE = {}
WAVE_BUCKETS = 900      # enough detail for the widest lane, cheap to resample
# ...and the ceiling once the lanes are zoomed in far enough that 900 buckets
# would draw a staircase. 57 600 buckets over a five-minute take is one peak
# per five milliseconds, which is finer than a pixel at the closest zoom this
# app offers, and the whole cache is a list of floats per resolution held.
WAVE_BUCKETS_MAX = 57600


def wave_peaks(path, buckets=None):
    """Per-bucket peak amplitude of a take, 0.0..1.0, or [] if unreadable.

    A recorded clip used to draw as a plain filled rectangle — the take was
    there and audible but the lane showed nothing about it, so there was no way
    to see where a phrase started, whether anything had been picked up, or
    where to cut. This is the shape that gets drawn inside the clip.

    Cached on (path, size, mtime): a lane redraws on every playhead tick and
    re-reading a WAV each time would be a file read per frame. Normalised to
    the take's own loudest sample so a quiet recording is still legible; a
    silent take normalises to nothing and correctly draws flat."""
    buckets = buckets or WAVE_BUCKETS
    try:
        st = os.stat(path)
        key = (path, st.st_size, int(st.st_mtime), buckets)
    except OSError:
        return []
    hit = _WAVE_CACHE.get(key)
    if hit is not None:
        return hit
    peaks = []
    try:
        import wave
        with wave.open(path, "rb") as w:
            if w.getsampwidth() != 2:
                return []
            n = w.getnframes()
            ch = max(1, w.getnchannels())
            raw = w.readframes(n)
        a = array.array("h")
        a.frombytes(raw[:len(raw) - (len(raw) % 2)])
        if sys.byteorder == "big":
            a.byteswap()
        if ch > 1:
            a = a[::ch]                     # one channel is a lane
        total = len(a)
        if total:
            step = max(1, total // buckets)
            top = 1
            for i in range(0, total, step):
                chunk = a[i:i + step]
                if chunk:
                    v = max(max(chunk), -min(chunk))
                    peaks.append(v)
                    top = max(top, v)
            peaks = [min(1.0, v / float(top)) for v in peaks]
    except Exception:                       # noqa: BLE001
        peaks = []
    if len(_WAVE_CACHE) > 96:               # a project has far fewer takes
        _WAVE_CACHE.clear()
    _WAVE_CACHE[key] = peaks
    return peaks


def open_capture_path(device):
    """Un-mute and raise the capture controls on the card `device` lives on.

    ALSA comes up with the CAPTURE side muted and at zero on a fresh state, and
    session.sh only ever un-muted the playback controls. The effect was not an
    error anywhere: arecord opened the device happily, wrote a valid WAV header
    and filled the file with silence, so a take recorded, appeared in the lane,
    played back, and could not be heard. That is indistinguishable from the
    microphone being broken.

    Done here, immediately before a take, rather than only at boot, because the
    common case is a USB microphone plugged in AFTER the machine started — boot
    never saw it.

    Three different things have to be true, and they are separate controls:
      * the capture SWITCH must be on   -> `cap`  (not `unmute`; different flag)
      * the capture VOLUME must be up   -> a percentage
      * on HDA, the mic BOOST is often 0, which records a technically valid but
        inaudible signal
    Every call is best-effort: a control a card does not have is not an error,
    and none of this may stop a recording that would otherwise work.
    """
    if not _have("amixer"):
        return
    card = None
    m = re.search(r":(\d+),", device or "")
    if m:
        card = m.group(1)
    base = ["amixer", "-q"] + (["-c", card] if card else []) + ["-M"]

    def _try(*args):
        try:
            subprocess.run(base + ["sset"] + list(args),
                           capture_output=True, timeout=4)
        except Exception:               # noqa: BLE001
            pass

    for ctl in ("Capture", "Mic", "Internal Mic", "Front Mic", "Rear Mic",
                "Line", "Line In", "Digital"):
        _try(ctl, "cap")
        _try(ctl, "unmute")
    _try("Capture", "80%", "cap")
    _try("Mic", "80%", "unmute")
    for ctl in ("Mic Boost", "Internal Mic Boost", "Front Mic Boost"):
        _try(ctl, "50%")


# A monitor that has fallen further behind than this has stopped being a
# monitor and become a delay, so the backlog is thrown away rather than played
# late for the rest of the take. A quarter of a second at the capture format.
MON_MAX_BACKLOG = CAP_RATE // 4 * 2


class Recorder:
    """Captures one take — and lets it be HEARD while it is being captured.

    `arecord` is asked for raw frames on its stdout instead of a .wav on the
    disk, and a pump thread here does three things with every chunk as it
    arrives:

      * writes it into the take's WAV (this program owns the header, so a take
        is a normal file the rest of the OS can open),
      * remembers the loudest sample in it, which is what the armed track's
        meter reads while recording — a level you can only see after the take
        is a level you set by guessing, and
      * with monitoring on, hands it to an `aplay` playing straight back out.

    THE TAKE IS SACRED; THE MONITOR IS NOT. The write to the WAV happens first
    and always, and the pipe to aplay is NON-BLOCKING: a chunk that will not fit
    waits in a small backlog and is dropped if that backlog grows. So a monitor
    that falls behind costs a click in the speakers and never a hole in the
    recording — which is exactly what a blocking write would cost instead, since
    a stalled aplay would stall the pump, fill arecord's pipe, and lose whatever
    the sound card had nowhere to put.

    A subprocess rather than a GStreamer pipeline, because arecord is already on
    the image and speaks straight to ALSA, so a USB interface works with no
    extra plumbing."""

    CHUNK = 4096              # bytes per read: ~43 ms at the capture format

    def __init__(self):
        self.proc = None
        self.path = None
        self.monitoring = False      # a monitor is actually running
        self.monitor_failed = False  # ...one was asked for and would not start
        self._mon = None
        self._mon_buf = b""
        self._wav = None
        self._thread = None
        self._stop = threading.Event()
        self._peak = 0.0             # loudest sample of the last chunk, 0..1
        self._frames = 0

    def start(self, device, path, monitor=False):
        """Begin capturing to `path`. Returns (ok, plain-English message).

        The messages are what the user reads in the status line, so they say
        what happened in the app's own terms — never the name of the recording
        program or a raw system error."""
        if not _have("arecord"):
            return False, "Recording isn't available on this computer"
        self.stop()
        self.monitoring = False
        self.monitor_failed = False
        self._peak = 0.0
        self._frames = 0
        self._mon_buf = b""
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        except OSError:
            return False, "Couldn't make a folder to keep the recording in"
        # The input is muted on a fresh ALSA state and a mic plugged in after
        # boot was never touched at all; without this the take is a valid WAV
        # full of silence. See open_capture_path.
        open_capture_path(device)
        try:
            wav = wave.open(path, "wb")
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(CAP_RATE)
        except Exception:            # noqa: BLE001
            return False, "Couldn't make a file to keep the recording in"
        cmd = ["arecord", "-D", device or "default", "-q",
               "-f", CAP_FMT, "-r", str(CAP_RATE), "-c", "1", "-t", "raw"]
        try:
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError:
            self.proc = None
            try:
                wav.close()
                os.unlink(path)
            except OSError:
                pass
            return False, "Couldn't start recording"
        self._wav = wav
        self.path = path
        if monitor:
            self._start_monitor()
        self._stop.clear()
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()
        return True, ""

    def start_monitor(self):
        """Turn monitoring on part-way through a take. No-op when idle."""
        if self.proc is not None and self._mon is None:
            self._mon_buf = b""
            self.monitor_failed = False
            self._start_monitor()

    def stop_monitor(self):
        """Turn monitoring off part-way through a take, leaving the recording
        alone. This is the control somebody reaches for while a microphone is
        howling into the speakers, so it takes effect on THIS take."""
        mon, self._mon = self._mon, None
        self.monitoring = False
        self._mon_buf = b""
        if mon is None:
            return
        try:
            mon.stdin.close()
        except Exception:               # noqa: BLE001
            pass
        try:
            mon.terminate()
            mon.wait(timeout=2)
        except Exception:               # noqa: BLE001
            pass

    def _start_monitor(self):
        """Play the input back out of the speakers as it comes in.

        Best-effort by design: a machine with no output, or one whose only
        output is busy, must still RECORD. Failure sets monitor_failed so the
        app can say the monitor is off rather than leaving someone waiting to
        hear something that is never coming."""
        if not _have("aplay"):
            self.monitor_failed = True
            return
        cmd = ["aplay", "-q", "-D", "default", "-t", "raw",
               "-f", CAP_FMT, "-r", str(CAP_RATE), "-c", "1",
               "-B", str(MON_BUFFER_US), "-F", str(MON_PERIOD_US)]
        try:
            self._mon = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
            os.set_blocking(self._mon.stdin.fileno(), False)
        except (OSError, ValueError):
            self._mon = None
            self.monitor_failed = True
            return
        self.monitoring = True

    def _pump(self):
        """Read the input until it ends. Runs on its own thread."""
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        fd = proc.stdout.fileno()
        while not self._stop.is_set():
            try:
                chunk = os.read(fd, self.CHUNK)
            except (OSError, ValueError):
                break
            if not chunk:
                break                       # arecord ended or was stopped
            wav = self._wav
            if wav is not None:
                try:
                    wav.writeframes(chunk)
                    self._frames += len(chunk) // 2
                except Exception:           # noqa: BLE001
                    break                   # a full disk: stop, keep what landed
            self._peak = _chunk_peak(chunk)
            if self._mon is not None:
                self._feed_monitor(chunk)

    def _feed_monitor(self, chunk):
        """Hand a chunk to aplay without ever waiting for it."""
        mon = self._mon
        if mon is None or mon.stdin is None:
            return
        buf = self._mon_buf + chunk
        if len(buf) > MON_MAX_BACKLOG:
            drop = len(buf) - MON_MAX_BACKLOG
            drop -= drop % 2                # never split a 16-bit frame
            buf = buf[drop:]
        try:
            n = os.write(mon.stdin.fileno(), buf)
        except BlockingIOError:
            n = 0
        except (OSError, ValueError):
            self._mon = None                # aplay died; the take carries on
            self.monitoring = False
            self._mon_buf = b""
            return
        self._mon_buf = buf[n:]

    def level(self):
        """The loudest sample of the last chunk read, 0..1. 0 when idle."""
        return self._peak if self.proc is not None else 0.0

    def stop(self):
        """End the take. Returns the WAV path if one was actually written."""
        p, path, wav = self.proc, self.path, self._wav
        thread, mon = self._thread, self._mon
        self.proc, self.path, self._wav = None, None, None
        self._thread, self._mon = None, None
        self.monitoring = False
        self._peak = 0.0
        if p is None:
            return None
        self._stop.set()
        try:
            p.terminate()
            try:
                p.wait(timeout=3)
            except Exception:               # noqa: BLE001
                p.kill()
                p.wait(timeout=2)
        except Exception:                   # noqa: BLE001
            pass
        # The pump exits as soon as arecord's stdout closes, which the kill
        # above guarantees. Joined before the file is closed so the header is
        # written over a WAV nothing is still appending to.
        if thread is not None:
            thread.join(timeout=3)
        if wav is not None:
            try:
                wav.close()
            except Exception:               # noqa: BLE001
                pass
        if mon is not None:
            try:
                mon.stdin.close()
            except Exception:               # noqa: BLE001
                pass
            try:
                mon.terminate()
                mon.wait(timeout=2)
            except Exception:               # noqa: BLE001
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
            err = p.stderr.read()    # drain, so the child can't block on a pipe
        except Exception:
            err = b""
        # Not shown to the user — it is program output — but it is the only
        # description of WHY a device refused, and discarding it entirely left
        # a dead input with no trace anywhere on the machine. Onto stderr,
        # which is where the rest of this app's diagnostics go.
        if err:
            try:
                sys.stderr.write("sequencer: capture ended early: %s\n"
                                 % err.decode("utf-8", "replace").strip())
                sys.stderr.flush()
            except Exception:       # noqa: BLE001
                pass
        return True


def _chunk_peak(chunk):
    """The loudest sample in a block of 16-bit mono PCM, 0..1."""
    buf = array.array("h")
    try:
        buf.frombytes(chunk[:len(chunk) - (len(chunk) % 2)])
    except (ValueError, EOFError):
        return 0.0
    if not buf:
        return 0.0
    return max(max(buf), -min(buf)) / 32768.0


class VU(Gtk.DrawingArea):
    """A segment level meter, laid out along a lane or up a channel strip."""
    def __init__(self, count, seg_w=7, seg_h=14, gap=2, bg=RAIL):
        super().__init__()
        self.count = count
        self.level = 0.0
        self.seg_w = seg_w
        self.seg_h = seg_h
        self.gap = gap
        self.bg = bg
        self.vertical = False
        self.set_size_request(count * (seg_w + gap) - gap, seg_h)
        self.set_valign(Gtk.Align.CENTER)
        self.connect("draw", self._draw)

    def set_vertical(self, on=True):
        """Stand the meter up, loud at the top — how a mixer reads."""
        self.vertical = bool(on)
        if self.vertical:
            self.set_size_request(self.seg_w,
                                  self.count * (self.seg_h + self.gap)
                                  - self.gap)
            self.set_valign(Gtk.Align.FILL)

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
            if self.vertical:
                y = (self.count - 1 - i) * (self.seg_h + self.gap)
                _rrect(cr, 0, y, self.seg_w, self.seg_h, 1)
            else:
                _rrect(cr, i * (self.seg_w + self.gap), 0,
                       self.seg_w, self.seg_h, 1)
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
    """One track's tape lane: the grid, its clips, the rec region, the playhead.

    THE LANE DRAWS A WINDOW ONTO THE TAPE, not the whole tape. Everything here
    goes through the app's view (see Sequencer.time_at_px / px_of_time), so
    zooming in is a change of one number rather than a different drawing path,
    and the ruler above cannot drift away from the clips it is numbering because
    both ask the same object where a second belongs.

    Every gesture an arrangement needs lives on this widget:

      * press a clip and drag it — along its lane, or up and down onto another
      * press either END of a clip and drag it — trims that end, and trimming
        the front moves the clip's read position into its take by the same
        amount, so the audio that is left does not slide in time
      * with the CUT tool, a click cuts the clip under it in two
      * a drag on empty tape picks the bars to loop
      * right-click removes a clip

    ...and each of them lands where SNAP says it may: on the bar, on the beat,
    on a sixteenth, or exactly where the pointer is.

    On the GPU-less hardware framebuffer the CPU rasterises every expose, so the
    static content (grid, loop band, committed clips, empty-state label) is
    rendered once into a cached ImageSurface and only rebuilt when it actually
    changes — the cache key carries every input to that drawing, the view window
    included. Each frame then costs one surface blit plus the cheap moving
    overlay. Crucially the per-tick playhead sweep goes through sync(), which
    invalidates only the thin strip the head moves across (queue_draw_area)
    rather than the whole lane. That is also why a running transport SCROLLS BY
    THE PAGE rather than gliding: a view that moved every tick would rebuild
    every lane's cache ten times a second, which is precisely the cost this
    whole arrangement exists to avoid."""
    DRAG_PX = 4      # a press that moves less than this is a click, not a drag
    EDGE = 7         # pixels either side of a clip end that grab it for a trim
    MIN_CLIP = 0.02  # a clip may never be trimmed or cut shorter than this

    def __init__(self, app, idx):
        super().__init__()
        self.app = app
        self.idx = idx
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                        | Gdk.EventMask.BUTTON_RELEASE_MASK
                        | Gdk.EventMask.POINTER_MOTION_MASK
                        | Gdk.EventMask.LEAVE_NOTIFY_MASK
                        | Gdk.EventMask.SCROLL_MASK
                        | Gdk.EventMask.SMOOTH_SCROLL_MASK
                        | Gdk.EventMask.BUTTON1_MOTION_MASK)
        self.connect("draw", self._draw)
        self.connect("button-press-event", self._click)
        self.connect("motion-notify-event", self._motion)
        self.connect("button-release-event", self._release)
        self.connect("leave-notify-event", self._leave)
        self.connect("scroll-event", self._scroll)
        # (static_key, ImageSurface, (view_start, view_span)) or None. The
        # view it was rendered AT rides along so a frame mid-animation can
        # stretch it to the view now — see _blit_moving.
        self._cache = None
        self._play_px = None      # last drawn playhead x, for strip invalidation
        self._press = None        # (x, y) where a drag started, in widget px
        self._loop_drag = None    # (t0, t1) being dragged out, or None
        self._move = None         # (clip, grab seconds, track it is on now)
        self._trim = None         # (clip, "in"|"out", original clip dict)
        self._moved = False
        self._hover = (None, None)   # ("in"|"out"|"body"|None, clip or None)
        self._hover_t = None      # where the CUT tool would cut, in seconds

    # -- geometry --------------------------------------------------------
    def _W(self):
        a = self.get_allocation()
        return a.width

    def _time_at(self, x):
        """Tape position in seconds under widget x, clamped to the tape."""
        return max(0.0, min(self.app.length, self.app.time_at_px(x, self._W())))

    def _x_of(self, t):
        return self.app.px_of_time(t, self._W())

    def _clips(self):
        return self.app.tracks[self.idx]["clips"]

    def _clip_at(self, t):
        # Last one wins: clips are drawn in list order, so the one on TOP is the
        # one a click should find.
        got = None
        for c in self._clips():
            if c["s"] <= t <= c["e"]:
                got = c
        return got

    def _probe(self, x):
        """What is under widget x: ("in"|"out"|"body"|None, clip or None).

        An END beats a BODY, and the ends are tested against PIXELS rather than
        seconds so the handle is the same size to the hand at every zoom — at
        the whole-song zoom a fixed number of seconds is a hair, and at the
        closest zoom it is most of the window."""
        best = None
        for c in self._clips():
            xi, xo = self._x_of(c["s"]), self._x_of(c["e"])
            # A clip too narrow to hold two handles is a body only; two hot
            # zones inside six pixels means it can be trimmed but never moved.
            if xo - xi > self.EDGE * 2.5:
                if abs(x - xi) <= self.EDGE:
                    return "in", c
                if abs(x - xo) <= self.EDGE:
                    return "out", c
            if xi <= x <= xo:
                best = ("body", c)
        return best if best else (None, None)

    # -- the pointer -----------------------------------------------------
    def _cursor(self, name):
        win = self.get_window()
        if win is None:
            return
        try:
            cur = (Gdk.Cursor.new_from_name(self.get_display(), name)
                   if name else None)
            win.set_cursor(cur)
        except Exception:            # noqa: BLE001
            pass

    def _leave(self, *_a):
        if self._hover != (None, None) or self._hover_t is not None:
            self._hover, self._hover_t = (None, None), None
            self.queue_draw()
        return False

    # -- gestures --------------------------------------------------------
    def _click(self, w, ev):
        # A seek while recording would move the playhead mid-take and
        # corrupt/silently discard the in-progress take — ignore playhead
        # moves during recording so an accidental click can't ruin a take.
        if self.app.transport == "rec":
            return True
        t = self._time_at(ev.x)
        if ev.button == 3:
            self.app.remove_part(self.idx, t)
            return True
        if ev.button != 1:
            return True
        if self.app.tool == TOOL_CUT:
            # The knife. Where it lands is SNAP's business, exactly as a move
            # or a trim is — cut_clip_at asks the grid itself, so the preview
            # line drawn under the pointer and the cut that follows it can
            # never be answering to different rules.
            self.app.cut_clip_at(self.idx, t)
            return True
        where, c = self._probe(ev.x)
        if ev.type == Gdk.EventType._2BUTTON_PRESS:
            # A clip is a container; opening it is what a second click on a
            # container means everywhere else in the OS.
            if c is not None:
                self.app.open_clip(self.idx, c)
            return True
        self._press = (ev.x, ev.y)
        self._loop_drag = None
        self._move = self._trim = None
        self._moved = False
        if c is not None:
            self.app.select_clip(self.idx, c)
            if where in ("in", "out"):
                self._trim = (c, where, clip_copy(c))
            else:
                self._move = (c, t - c["s"], self.idx)
            return True
        self.app.pos = self.app.snap_time(t)
        self.app.refresh()
        return True

    def _motion(self, w, ev):
        if self.app.transport == "rec":
            return False
        if self._press is None:
            return self._hover_motion(ev)
        px, _py = self._press
        if abs(ev.x - px) < self.DRAG_PX and not self._moved:
            return False
        if self._trim is not None:
            return self._drag_trim(ev)
        if self._move is not None:
            return self._drag_move(ev)
        self._loop_drag = (self._time_at(px), self._time_at(ev.x))
        self.queue_draw()
        return True

    def _hover_motion(self, ev):
        """No button down: keep the pointer shape and the cut preview honest."""
        if self.app.tool == TOOL_CUT:
            t = self.app.snap_time(self._time_at(ev.x))
            over = self._clip_at(t) is not None
            self._cursor("crosshair" if over else "default")
            if self._hover_t != t or self._hover != (None, None):
                self._hover, self._hover_t = (None, None), t
                self.queue_draw()
            return False
        got = self._probe(ev.x)
        if self._hover_t is not None:
            self._hover_t = None
            self.queue_draw()
        if got != self._hover:
            self._hover = got
            self.queue_draw()
        self._cursor({"in": "w-resize", "out": "e-resize",
                      "body": "grab"}.get(got[0]) or "default")
        return False

    def _drag_trim(self, ev):
        """Drag one end of a clip. The take is never cut, only looked at less.

        Trimming the START also moves the clip's read position into its take by
        the same amount, so the audio that is left does not slide in time —
        cutting a count-in off the front must not move the take that follows
        it. Neither end may travel past what the take actually holds: a clip is
        a window onto a recording and cannot show what was never recorded."""
        c, which, orig = self._trim
        if not self._moved:
            self.app.remember_edit("Trim")
            self._moved = True
        t = self.app.snap_time(self._time_at(ev.x))
        avail = clip_take_len(c)
        if which == "in":
            lo = orig["s"] - orig["off"]         # the head of the take
            t = max(lo, min(c["e"] - self.MIN_CLIP, max(0.0, t)))
            d = t - c["s"]
            c["s"] += d
            c["off"] = max(0.0, c["off"] + d)
        else:
            hi = self.app.length
            if avail is not None:
                hi = min(hi, orig["s"] - orig["off"] + avail)
            t = max(c["s"] + self.MIN_CLIP, min(hi, t))
            c["e"] = t
        self.queue_draw()
        self.app.sync_ruler()
        return True

    def _drag_move(self, ev):
        """Drag a clip along its lane — and, if the pointer leaves the lane
        vertically, onto another one.

        Every lane is the same height and they are stacked, so how many lanes
        away the pointer is is just how many lane-heights outside this one it
        has travelled. Moving between tracks is what an arrangement is FOR (the
        second take of a chorus belongs beside the first, not on top of it), and
        it was the one thing a clip could not do."""
        c, grab, ti = self._move
        if not self._moved:
            self.app.remember_edit("Move")
            self._moved = True
        span = c["e"] - c["s"]
        s = self.app.snap_time(self._time_at(ev.x) - grab)
        s = max(0.0, min(self.app.length - span, s))
        if abs(s - c["s"]) > 1e-9:
            c["s"], c["e"] = s, s + span
            # the clip may have travelled to another lane, and that is the one
            # that has to repaint — this widget still owns the pointer grab
            try:
                self.app.lanes[ti].queue_draw()
            except (AttributeError, IndexError):
                self.queue_draw()
        H = max(1, self.get_allocated_height())
        rows = int(math.floor(ev.y / H)) if (ev.y < 0 or ev.y >= H) else 0
        # rows is measured from THIS lane (the pointer never leaves its grab),
        # so the target is always relative to where the drag began.
        want = max(0, min(TRACKS - 1, self.idx + rows))
        if want != ti and self.app.move_clip_to_track(ti, c, want):
            self._move = (c, grab, want)
        return True

    def _release(self, w, _ev):
        loop, moved = self._loop_drag, self._moved
        self._press = self._loop_drag = self._move = self._trim = None
        self._moved = False
        if self.app.transport == "rec":
            return False
        if moved:
            # _moved is set by the first motion of a move OR a trim, so this
            # covers both — and a press on a handle that never travelled is
            # not an edit and does not get saved and re-rendered.
            self.app.clip_changed()
            return True
        if loop is None:
            return False
        self.queue_draw()
        # A drag across empty tape selects the bars, which is what the ruler
        # above does too — an empty audio clip is a block that can never make a
        # sound, so there is nothing else for the gesture to mean.
        s, e = self.app.snap_span(min(loop), max(loop))
        self.app._set_loop(s, e)
        return True

    def _scroll(self, _w, ev):
        """Ctrl+wheel zooms about the pointer; Shift+wheel scrolls sideways.

        A plain wheel is left alone so it still scrolls the stack of tracks,
        which is what a wheel does everywhere else in the OS."""
        return self.app.wheel_over_timeline(ev, self._W(), self._time_at)

    # -- painting --------------------------------------------------------
    def _static_key(self, W, H):
        """Everything the cached surface depends on. A mismatch means the
        surface is stale and both it and the whole lane must be repainted."""
        tk = self.app.tracks[self.idx]
        rec = (self.app.transport == "rec" and tk["armed"]
               and self.app.rec_start is not None)
        # the first lane carries the how-to-record hint until the tape has
        # anything on it, so whether the WHOLE project is empty is part of its
        # static content, not just this track's clips
        sel = self.app.sel_clip()
        return (W, H, self.app._audible(tk), round(self.app.length, 3),
                round(self.app.view_start, 5), round(self.app.zoom, 4),
                self.app.snap, self.app.bpm,
                (self.app.loop_s, self.app.loop_e, self.app.loop_on),
                tuple((c["s"], c["e"], c.get("wav"), c.get("gain"),
                       c.get("fin"), c.get("fout"), id(c) == id(sel))
                      for c in tk["clips"]),
                bool(tk["armed"]), rec,
                (self._hover[0], id(self._hover[1])), self.app.tool,
                self.idx == 0 and self.app.is_empty())

    def _paint_grid(self, cr, W, H):
        """The bar lines, and the beats and grid steps inside them once there
        is room to tell them apart.

        A LANE WITHOUT A GRID CANNOT BE CUT ON ONE. Snapping put edits on the
        bar, but nothing on screen said where the bar was — the numbered ruler
        is one strip at the top of eight lanes — so an edit landing on the grid
        looked like an edit landing wherever it felt like."""
        app = self.app
        spb = app.sec_per_bar()
        if spb <= 0:
            return
        bar_px = self._x_of(spb) - self._x_of(0.0)
        if bar_px < 3:
            return                               # denser than it is readable
        t0 = app.view_start
        t1 = t0 + app.view_span()
        first = int(math.floor(t0 / spb))
        # The fine steps earn their way onto the paper. Beats arrive first;
        # snap subdivisions need enough air that repeated hairlines cannot
        # merge into a grey graph-paper field.
        beat = app.sec_per_beat()
        steps = []
        if bar_px / BEATS_PER_BAR >= 10:
            steps.append((beat, GRID_BEAT_ALPHA))
        gs = app.snap_seconds()
        if (gs and gs < beat - 1e-9
                and (self._x_of(gs) - self._x_of(0.0)) >= 12):
            steps.append((gs, GRID_SUB_ALPHA))
        b = first
        while True:
            at = b * spb
            if at > t1 + spb:
                break
            x = self._x_of(at)
            if at >= -1e-9:
                cr.set_source_rgba(*INK, GRID_BAR_ALPHA)
                cr.rectangle(round(x), 0, 1, H)
                cr.fill()
            for size, alpha in steps:
                k = size
                while k < spb - 1e-9:
                    sx = self._x_of(at + k)
                    if 0 <= sx <= W:
                        cr.set_source_rgba(*INK, alpha)
                        cr.rectangle(round(sx), 0, 1, H)
                        cr.fill()
                    k += size
            b += 1
        # A lane ends on a paper rule, not another time division. Its solid
        # beige tone is intentionally unlike every translucent ink rule above.
        cr.set_source_rgb(*CENTER)
        cr.rectangle(0, H - 1, W, 1)
        cr.fill()

    def _paint_static(self, cr, W, H):
        """Render the lane's slow-changing layer (everything except the moving
        rec region and playhead). Runs on a cache miss only, never per tick."""
        app = self.app
        tk = app.tracks[self.idx]
        audible = app._audible(tk)
        rec = (app.transport == "rec" and tk["armed"]
               and app.rec_start is not None)
        # background
        if not audible:
            cr.set_source_rgb(0xF1 / 255, 0xEE / 255, 0xE6 / 255)
        else:
            cr.set_source_rgb(0xFC / 255, 0xFB / 255, 0xF8 / 255)
        cr.rectangle(0, 0, W, H)
        cr.fill()
        # the looped stretch, so the bars going round are visible against the
        # clips themselves and not only up on the ruler
        if app.loop_on and app.loop_e - app.loop_s > 0.001:
            lx = self._x_of(app.loop_s)
            lw = self._x_of(app.loop_e) - lx
            cr.set_source_rgba(*INK, 0.045)
            cr.rectangle(lx, 0, max(1, lw), H)
            cr.fill()
        self._paint_grid(cr, W, H)
        # centre hairline
        cr.set_source_rgb(*CENTER)
        cr.rectangle(0, int(H / 2), W, 1)
        cr.fill()
        top = H * 0.14
        ch = H * 0.72
        # committed clips
        sel = app.sel_clip()
        hov_kind, hov_clip = self._hover
        for _c in tk["clips"]:
            s, e, _wav = clip_parts(_c)
            x = self._x_of(s)
            cw = self._x_of(e) - x
            if x > W or x + cw < 0 or cw <= 0:
                continue                     # off the side of the window
            cw = max(2.0, cw)
            if not audible:
                cr.set_source_rgb(0xDD / 255, 0xD6 / 255, 0xC6 / 255)
            else:
                cr.set_source_rgb(0xCB / 255, 0xBF / 255, 0xA0 / 255)
            _rrect(cr, x, top, cw, ch, 2)
            cr.fill()
            chosen = _c is sel
            cr.set_source_rgba(*INK, 0.9 if chosen else 0.42)
            cr.set_line_width(2 if chosen else 1)
            _rrect(cr, x + 0.5, top + 0.5, cw - 1, ch - 1, 2)
            cr.stroke()
            cr.set_line_width(1)
            self._paint_wave(cr, _c, x, cw, H, top, ch, audible)
            self._paint_fades(cr, _c, x, cw, top, ch)
            # the handles, shown on the clip the pointer is over so the two
            # places a press means "trim" rather than "move" are visible
            # BEFORE it is pressed
            if _c is hov_clip and hov_kind in ("in", "out", "body"):
                for name, hx in (("in", x), ("out", x + cw)):
                    lit = (hov_kind == name)
                    cr.set_source_rgba(*(RED if lit else INK),
                                       1.0 if lit else 0.35)
                    cr.rectangle(round(hx) - 1, top, 2, ch)
                    cr.fill()
            if chosen and cw > 54:
                self._paint_label(cr, _c, x, cw, top)
        # empty label (only when this lane holds nothing and isn't recording).
        # On a brand-new arrangement the first lane says HOW to fill it —
        # eight lanes all reading "Empty" told a first-time user nothing about
        # what to do next.
        if not tk["clips"] and not rec:
            if tk["armed"]:
                label = _t("Armed — press Record")
            elif app.is_empty() and self.idx == 0:
                label = _t("Press REC on this lane, then Record, to capture "
                           "what you play")
            else:
                label = _t("Empty")
            cr.set_source_rgb(*FAINT)
            # Pango, not cairo's toy font API. The toy API resolves ONE face and
            # draws .notdef for anything that face lacks, so the moment this
            # hint became translatable it would have come out as empty boxes in
            # Chinese. Pango falls back per glyph, so it reads in every
            # language. Painted on a cache miss only, never per frame.
            _pango(cr, label, 14, 11, 11.5)

    def _paint_wave(self, cr, c, x, cw, H, top, ch, audible):
        """The take's own shape, so a lane shows WHAT was recorded rather than
        only that something was — and shows it at the clip's own level, because
        a fader that changes nothing on screen is a fader you set twice."""
        _s, _e, wav = clip_parts(c)
        if not wav:
            cr.set_source_rgba(*INK, 0.28)
            cr.rectangle(x + 6, int(H / 2), max(0, cw - 12), 1)
            cr.fill()
            return
        # Only the visible slice is drawn, and only the peaks under it are
        # asked for: zoomed in on one word of a vocal, the clip may be a mile
        # wide and one pixel of it on screen.
        W = self._W()
        vx0 = max(x, 0.0)
        vx1 = min(x + cw, float(W))
        if vx1 - vx0 < 1:
            return
        span = max(1e-9, c["e"] - c["s"])
        dur = take_length(wav) or span
        # HOW FINE THE PEAKS HAVE TO BE DEPENDS ON THE ZOOM. The cache holds
        # one peak per bucket over the WHOLE take, which is ample at the
        # whole-song zoom and a staircase at the closest one — a hundred pixels
        # all reading the same bucket. Ask for as many buckets as the visible
        # slice needs, quantised in big steps so the cache holds two or three
        # resolutions rather than one per zoom level (each is a full file read).
        # one pixel of the clip is span/cw seconds of the take, so the whole
        # take needs dur / (span/cw) buckets to give every pixel its own
        need = dur * cw / span
        buckets = WAVE_BUCKETS
        while buckets < need and buckets < WAVE_BUCKETS_MAX:
            buckets *= 8
        peaks = wave_peaks(wav, min(buckets, WAVE_BUCKETS_MAX))
        if not peaks:
            return
        mid = H / 2.0
        half = (ch / 2.0) - 2
        g = max(0.05, min(4.0, c.get("gain", 1.0)))
        cr.set_source_rgba(*INK, 0.55 if audible else 0.3)
        n = len(peaks)
        cols = int(vx1 - vx0)
        for i in range(cols):
            # where this pixel column sits inside the TAKE, not the clip
            f0 = (vx0 + i - x) / cw
            f1 = (vx0 + i + 1 - x) / cw
            a = (c["off"] + f0 * span) / dur
            b = (c["off"] + f1 * span) / dur
            lo = max(0, min(n - 1, int(a * n)))
            hi = max(lo + 1, min(n, int(b * n)))
            v = max(peaks[lo:hi])
            hgt = max(0.7, min(1.0, v * g) * half)
            cr.rectangle(vx0 + i, mid - hgt, 1, hgt * 2)
        cr.fill()

    def _paint_fades(self, cr, c, x, cw, top, ch):
        """The fade ramps, as the RAMP: a line from the clip's bottom corner up
        to where the fade reaches full.

        Drawn as a stroke and not as a filled corner, which is what this was
        first: filled, in the same ink as the waveform, a half-second fade on a
        quiet take was a solid wedge indistinguishable from loud audio — the
        picture said the opposite of what the fade does."""
        span = max(1e-9, c["e"] - c["s"])
        fin = min(c.get("fin", 0.0), span * 0.5)
        fout = min(c.get("fout", 0.0), span * 0.5)
        cr.set_source_rgba(*INK, 0.5)
        cr.set_line_width(1.2)
        if fin > 0.02:
            cr.move_to(x + 0.5, top + ch)
            cr.line_to(x + cw * (fin / span), top + 1)
            cr.stroke()
        if fout > 0.02:
            cr.move_to(x + cw - cw * (fout / span), top + 1)
            cr.line_to(x + cw - 0.5, top + ch)
            cr.stroke()
        cr.set_line_width(1)

    def _paint_label(self, cr, c, x, cw, top):
        """How long the selected clip is, on the clip.

        Only the selected one: eight lanes of labelled blocks is a wall of
        numbers, but "is this take the same length as that one" is the question
        every loop turns on, and it should not need a calculator."""
        span = c["e"] - c["s"]
        bars = span / max(1e-9, self.app.sec_per_bar())
        txt = ("%.2f %s" % (bars, _t("bars")) if abs(bars - round(bars)) > 0.02
               else "%d %s" % (round(bars), _t("bars")))
        txt = "%s · %s" % (txt, _fmt_dur(span))
        # on a plate, because it is written OVER the waveform and ink on a
        # dense take is ink on ink
        w = min(cw - 8, 7.0 + 5.4 * len(txt))
        cr.set_source_rgba(0xF4 / 255, 0xF1 / 255, 0xE7 / 255, 0.88)
        _rrect(cr, x + 4, top + 2, w, 14, 2)
        cr.fill()
        cr.set_source_rgba(*INK, 0.8)
        _pango(cr, txt, x + 7, top + 3, 10.0, max(10, w - 6))

    def _paint_overlay(self, cr, W, H):
        """The cheap moving layer, blitted on top of the cached surface every
        frame: the active rec region (its leading edge grows with the playhead),
        the loop being dragged out, the cut preview, and the playhead itself.
        Records the drawn playhead x for sync()."""
        app = self.app
        tk = app.tracks[self.idx]
        top = H * 0.14
        ch = H * 0.72
        rec = (app.transport == "rec" and tk["armed"]
               and app.rec_start is not None)
        if rec:
            x = self._x_of(app.rec_start)
            cw = max(2, self._x_of(app.pos) - x)
            cr.set_source_rgba(*RED, 0.12)
            cr.rectangle(x, top, cw, ch)
            cr.fill()
            cr.set_source_rgb(*RED)
            cr.set_line_width(1)
            cr.set_dash([3, 3])
            cr.rectangle(x + 0.5, top + 0.5, cw - 1, ch - 1)
            cr.stroke()
            cr.set_dash([])
        # the bars being dragged out, shown already snapped so what is on
        # screen is exactly what will be got. Ink, not red — signage red
        # belongs to recording and alerts.
        if self._loop_drag is not None:
            s, e = app.snap_span(min(self._loop_drag), max(self._loop_drag))
            x = self._x_of(s)
            cw = max(2, self._x_of(e) - x)
            cr.set_source_rgba(*INK, 0.10)
            cr.rectangle(x, top, cw, ch)
            cr.fill()
            cr.set_source_rgb(*INK)
            cr.set_line_width(1)
            cr.set_dash([3, 3])
            cr.rectangle(x + 0.5, top + 0.5, cw - 1, ch - 1)
            cr.stroke()
            cr.set_dash([])
        # where the knife would land, before it lands
        if self._hover_t is not None and app.tool == TOOL_CUT:
            cx = self._x_of(self._hover_t)
            cr.set_source_rgb(*RED)
            cr.set_line_width(1)
            cr.set_dash([2, 3])
            cr.move_to(round(cx) + 0.5, 0)
            cr.line_to(round(cx) + 0.5, H)
            cr.stroke()
            cr.set_dash([])
        # The playhead is a POSITION, not an alert: ink, by the same rule the
        # drag preview above follows. In red it competed with the REC chips and
        # the recording region for the one signage colour, and it is on screen
        # at all times - including when nothing is being recorded at all.
        px = self._x_of(app.pos)
        cr.set_source_rgb(*INK)
        cr.rectangle(round(px) - 1, 0, 2, H)
        cr.fill()
        self._play_px = px

    def _blit_moving(self, cr, W, H):
        """Paint the cached layer STRETCHED from the view it was rendered at to
        the view the animation has reached. True if it could.

        THIS IS WHAT MAKES AN ANIMATED ZOOM AFFORDABLE HERE (Article F1). A view
        change invalidates every input to the cached surface, so easing the view
        the obvious way would re-rasterise eight full-width lanes on every frame
        of the animation — the grid, every clip, and a Python loop over a
        thousand waveform columns each — which is precisely the cost the cache
        exists to avoid on the software rasteriser.

        A zoom is a similarity transform, so the pixels already in hand ARE the
        answer, resampled: a surface rendered at (s0, span0) shown at
        (s1, span1) wants scale span0/span1 about the point where s0 now falls.
        Mid-flight it is a little soft; on arrival the animation lands and the
        next frame is a true render at the final view, which is the frame anyone
        actually reads. Two hundred milliseconds of resampling versus two
        hundred milliseconds of re-rasterising is the whole trade."""
        cache = self._cache
        if not cache or cache[2] is None:
            return False
        s0, span0 = cache[2]
        span1 = self.app.view_span()
        if span0 <= 0 or span1 <= 0:
            return False
        sx = span0 / span1
        # where the cached surface's left edge (time s0) sits in the view now
        x0 = (s0 - self.app.view_start) / span1 * W
        try:
            cr.save()
            # the lane is opaque; fill first or the uncovered edge keeps the
            # last frame's pixels while the view slides in from the side
            cr.set_source_rgb(*(RAIL if not self.app._audible(
                self.app.tracks[self.idx]) else SURF))
            cr.rectangle(0, 0, W, H)
            cr.fill()
            cr.translate(x0, 0)
            cr.scale(sx, 1.0)
            cr.set_source_surface(cache[1], 0, 0)
            try:
                import cairo
                cr.get_source().set_filter(cairo.FILTER_BILINEAR)
            except Exception:                                     # noqa: BLE001
                pass
            cr.paint()
            cr.restore()
        except Exception:                                         # noqa: BLE001
            try:
                cr.restore()
            except Exception:                                     # noqa: BLE001
                pass
            return False
        return True

    def _draw(self, w, cr):
        a = w.get_allocation()
        W, H = a.width, a.height
        if W < 4 or H < 4:            # not-yet / degenerately allocated
            return False
        cache = self._cache
        # While the view is travelling, stretch what is already rendered rather
        # than rendering again — see _blit_moving. Falls through to a real
        # render if there is nothing cached yet (the first frame of the app).
        if self.app.view_moving() and self._blit_moving(cr, W, H):
            self._paint_overlay(cr, W, H)
            return False
        key = self._static_key(W, H)
        if not (cache and cache[0] == key):
            try:
                import cairo
                # opaque static layer → RGB24 (no alpha channel to blit)
                surf = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
                self._paint_static(cairo.Context(surf), W, H)
                surf.flush()
                # the view this raster was taken AT travels with it, so a later
                # frame can work out how to stretch it (_blit_moving)
                cache = self._cache = (key, surf,
                                       (self.app.view_start,
                                        self.app.view_span()))
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
        new_px = self._x_of(self.app.pos)
        old_px = self._play_px if self._play_px is not None else new_px
        lo = int(max(0, min(old_px, new_px) - 3))
        hi = int(min(W, max(old_px, new_px) + 3))
        self.queue_draw_area(lo, 0, max(1, hi - lo), H)


class Ruler(Gtk.DrawingArea):
    """The bar grid over the lanes: where the playhead is put, and where the
    loop is dragged out.

    It measures the same window onto the tape the lanes draw, so it zooms with
    them, and it says as much about WHERE as there is room to say: bar numbers
    always, beats inside the bar once they are far enough apart to read, and
    the bar-and-beat spelled out ("17.3") once one bar is wide enough to hold
    four labels — which is the zoom at which somebody is placing an edit rather
    than looking at an arrangement."""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.set_hexpand(True)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                        | Gdk.EventMask.BUTTON_RELEASE_MASK
                        | Gdk.EventMask.SCROLL_MASK
                        | Gdk.EventMask.SMOOTH_SCROLL_MASK
                        | Gdk.EventMask.BUTTON1_MOTION_MASK)
        self.connect("draw", self._draw)
        self.connect("button-press-event", self._click)
        self.connect("motion-notify-event", self._motion)
        self.connect("button-release-event", self._release)
        self.connect("scroll-event", self._scroll)
        self.set_tooltip_text(
            _t("Click to move the playhead  ·  drag to choose the bars to "
               "loop  ·  Ctrl+wheel to zoom"))
        self._press_x = None
        self._drag = None

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

    def _time_at(self, x):
        x0, aw = self._axis()
        return max(0.0, min(self.app.length,
                            self.app.time_at_px(x - x0, aw)))

    def _x_of(self, t):
        x0, aw = self._axis()
        return x0 + self.app.px_of_time(t, aw)

    def _click(self, w, ev):
        # A seek while recording would move the playhead mid-take and
        # corrupt/silently discard the in-progress take — ignore playhead
        # moves during recording so an accidental click can't ruin a take.
        if self.app.transport == "rec":
            return True
        self._press_x = ev.x
        self._drag = None
        self.app.pos = self.app.snap_time(self._time_at(ev.x))
        self.app.refresh()
        return True

    def _motion(self, w, ev):
        """A drag across the ruler picks the bars to loop.

        The ruler measures the arrangement, so selecting a stretch of it is
        what dragging along it ought to mean — and it is the one gesture that
        makes practising over four bars, which is most of how anything gets
        recorded, take no setting up at all."""
        if self._press_x is None or self.app.transport == "rec":
            return False
        if self._drag is None and abs(ev.x - self._press_x) < 6:
            return False
        self._drag = (self._time_at(self._press_x), self._time_at(ev.x))
        self.queue_draw()
        return True

    def _release(self, w, _ev):
        drag = self._drag
        self._press_x, self._drag = None, None
        if drag is None or self.app.transport == "rec":
            return False
        s, e = self.app.snap_span(min(drag), max(drag))
        self.app._set_loop(s, e)
        return True

    def _scroll(self, _w, ev):
        x0, aw = self._axis()
        return self.app.wheel_over_timeline(
            ev, aw, self._time_at, plain_scrolls=True, x_offset=x0)

    def _draw(self, w, cr):
        a = w.get_allocation()
        W, H = a.width, a.height
        x0, aw = self._axis()
        app = self.app
        # opaque fill first — an unpainted DrawingArea window renders black on
        # the no-compositor framebuffer (matches the .rulerrow rail surface)
        cr.set_source_rgb(*RAIL)
        cr.rectangle(0, 0, W, H)
        cr.fill()
        # the looped stretch, behind the numbers
        band = self._drag
        if band is not None:
            ls, le = app.snap_span(min(band), max(band))
            on = True
        else:
            ls, le, on = app.loop_s, app.loop_e, app.loop_on
        if le - ls > 0.001:
            lx = self._x_of(ls)
            lw = max(2, self._x_of(le) - lx)
            cr.set_source_rgba(*INK, 0.16 if on else 0.07)
            cr.rectangle(lx, 0, lw, H)
            cr.fill()
            cr.set_source_rgba(*INK, 0.75 if on else 0.3)
            cr.rectangle(lx, 0, 1, H)
            cr.rectangle(lx + lw - 1, 0, 1, H)
            cr.fill()
        cr.select_font_face("Nimbus Sans", cairo_slant(), cairo_weight())
        cr.set_font_size(10.5)
        # A BAR ruler, not a clock: an arrangement is read in bars and beats, so
        # the grid lines fall on musical divisions and follow the tempo. Bars are
        # numbered; beats inside a bar get a short tick. When the arrangement is
        # long enough that every bar would collide, only every 2nd/4th/8th bar is
        # labelled — the ticks stay, so the grid is still countable.
        spb = app.sec_per_bar()
        if spb <= 0 or aw <= 0:
            return
        px_per_bar = self._x_of(spb) - self._x_of(0.0)
        if px_per_bar <= 0:
            return
        step = 1
        for cand in (1, 2, 4, 8, 16, 32, 64, 128):
            if px_per_bar * cand >= 46:      # room for a "123" label
                step = cand
                break
        beat_w = px_per_bar / BEATS_PER_BAR
        # Zoomed in far enough that four labels fit inside one bar, the beats
        # get named too: at this range the question has stopped being "which
        # part of the song" and become "which beat of which bar".
        name_beats = beat_w >= 54
        t0 = app.view_start
        t1 = t0 + app.view_span()
        first = max(0, int(math.floor(t0 / spb)))
        last = int(math.ceil(t1 / spb))
        for b in range(first, last + 1):
            x = self._x_of(b * spb)
            if x > x0 + aw + 2:
                break
            if x < x0 - px_per_bar:
                continue
            labelled = (b % step == 0)
            cr.set_source_rgba(*INK, GRID_BAR_ALPHA)
            cr.rectangle(round(x), 5, 1, 20 if labelled else 12)
            cr.fill()
            # beat ticks, only when they are far enough apart to read
            if beat_w >= 9:
                for k in range(1, BEATS_PER_BAR):
                    bx = x + k * beat_w
                    if bx >= x0 + aw:
                        break
                    cr.set_source_rgba(*INK, GRID_BEAT_ALPHA)
                    cr.rectangle(round(bx), 17, 1, 8)
                    cr.fill()
                    if name_beats:
                        cr.set_source_rgb(*FAINT)
                        cr.move_to(bx + 4, 19)
                        cr.show_text("%d.%d" % (b + 1, k + 1))
            if labelled:
                cr.set_source_rgb(*MUTED)
                cr.move_to(x + 5, 19)
                cr.show_text("%d" % (b + 1))     # bars count from 1

def _pango(cr, text, x, y, size, max_px=None):
    """Draw one line of UI text through Pango.

    Pango and never cairo's toy font API: the toy API resolves ONE face and
    draws .notdef for anything that face lacks, so any string that can be
    translated comes out as empty boxes in Chinese. Pango falls back per glyph.
    `max_px` ellipsizes rather than letting a long string run off its clip."""
    layout = PangoCairo.create_layout(cr)
    fd = Pango.FontDescription("Nimbus Sans")
    fd.set_absolute_size(size * Pango.SCALE)
    layout.set_font_description(fd)
    if max_px:
        layout.set_width(int(max_px) * Pango.SCALE)
        layout.set_ellipsize(Pango.EllipsizeMode.END)
    layout.set_text(text, -1)
    cr.move_to(x, y)
    PangoCairo.show_layout(cr, layout)


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


class AudioOut:
    """Streams the arrangement to the sound card while it is being rendered.

    nbsynth turns the song into stereo PCM; this class is the plumbing that
    gets it out of the machine. A background thread renders 512-frame blocks
    into a bounded queue and an ``appsrc ! audioconvert ! audioresample !
    autoaudiosink`` pipeline pulls them out on demand.

    THE QUEUE IS THE POINT. Rendering a bar of music in CPython is faster than
    playing it — comfortably, on ordinary hardware — but not uniformly: a bar
    where eight takes, both tone filters, a compressor and a delay tail all
    speak at once costs several times a bar of one voice. A renderer wired
    straight to the sink would have to be fast enough for the WORST bar or it
    would stutter through it. With a second of finished audio banked ahead, the
    expensive bar is paid for out of the bank and refilled during the cheap one.

    Everything is a silent no-op unless start() succeeds, so if GStreamer is
    missing (build host, selftests) or the audio device will not open,
    ``available`` stays False and the sequencer keeps working in silence.
    """

    # A failed sink is retried on the NEXT press of Play / Record, at most this
    # many times — the sound card may not have settled yet on the first play
    # after boot, another program may have had it for a moment, and Settings ▸
    # Sound can point `default` somewhere else while this window is open.
    RETRIES = 3

    def __init__(self):
        self.available = False    # True once the pipeline is actually streaming
        self.failed = False       # True once we tried and Gst / the sink refused
        self.underruns = 0        # blocks the sink asked for and did not get
        self.bypassed = False     # effects dropped to keep up (see _render_loop)
        self._pipe = None
        self._src = None
        self._tries = 0
        self._started = False
        self._thread = None
        self._stop = threading.Event()
        self._q = queue.Queue(maxsize=LOOKAHEAD_BLOCKS)
        self._mix = None
        self._pending = None      # an edited song the render thread must adopt
        self._lock = threading.Lock()
        self._t0 = None           # monotonic time the first block was pushed
        self._start_at = 0.0      # song position that block corresponds to
        self._pushed = 0          # frames handed to the sink so far
        self._next_song = 0       # song frame the next block should carry
        self._map = [(0, 0)]      # (pushed frame, song frame) at each loop wrap
        self._peaks = (0.0, 0.0)
        self._track_peaks = []
        self._silence = bytes(nbsynth.BLOCK * 4)

    # -- lifecycle -------------------------------------------------------
    def start(self, song, at=0.0, metronome=False):
        """Begin rendering `song` from `at` seconds and playing it. True on ok.

        `at` may be negative: that is the count-in, where the click sounds in
        the song's own tempo and nothing else has begun."""
        self.stop()
        if not GST_OK:
            self.failed = True
            return False
        if self._started and self._tries >= self.RETRIES:
            return False
        self._started = True
        self._tries += 1
        self.failed = False
        self.underruns = 0
        self.bypassed = False
        try:
            self._mix = nbsynth.Mixdown(song, at, metronome=metronome)
        except Exception:
            self._mix = None
            self.failed = True
            return False
        self._start_at = float(at)
        self._t0 = None
        self._pushed = 0
        self._next_song = int(round(float(at) * SR))
        self._map = [(0, self._next_song)]
        self._stop.clear()
        self._q = queue.Queue(maxsize=LOOKAHEAD_BLOCKS)
        # Fill the bank BEFORE the sink is allowed to ask for anything. The
        # first thing a listener hears is the first block, and starting the
        # pipeline against an empty queue means the first thing rendered is
        # also the first thing late.
        self._prime()
        self._thread = threading.Thread(target=self._render_loop, daemon=True)
        self._thread.start()
        try:
            Gst.init(None)
            self._pipe = Gst.parse_launch(
                "appsrc name=src is-live=true format=time do-timestamp=true "
                "emit-signals=true block=false ! audioconvert ! audioresample "
                "! autoaudiosink")
            self._src = self._pipe.get_by_name("src")
            self._src.set_property("caps", Gst.Caps.from_string(
                "audio/x-raw,format=S16LE,layout=interleaved,"
                "rate=%d,channels=2" % SR))
            # Keep the SINK's own buffer small: the lookahead lives in our
            # queue, where a mute or a fader move can still overtake it. Buried
            # in appsrc it would only add latency nothing can reach past.
            self._src.set_property("max-bytes", 8 * nbsynth.BLOCK * 4)
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

    def _prime(self):
        """Render up to half the lookahead before the sink is started."""
        try:
            for _ in range(LOOKAHEAD_BLOCKS // 2):
                if self._q.full():
                    break
                self._q.put_nowait(self._render_one())
        except Exception:
            pass

    def _render_one(self):
        mix = self._mix
        at = mix.frame                       # the song frame this block starts on
        data = mix.render(nbsynth.BLOCK)
        return (data, mix.peakL, mix.peakR, list(mix.track_peak), at)

    def _render_loop(self):
        """Keep the bank full. Runs off the GTK thread; touches no widget."""
        low = 0
        while not self._stop.is_set():
            try:
                with self._lock:
                    pend, self._pending = self._pending, None
                if pend is not None and self._mix is not None:
                    self._mix.resync(pend)
                # RUNNING BEHIND: DROP THE EFFECTS RATHER THAN THE SOUND.
                # The room, the echo and the tape are between a third and a
                # half of the cost of a block. On a computer that cannot render
                # the arrangement as fast as it plays, the choice is between an
                # arrangement with effects that stutters and one without them
                # that does not, and a stutter is not a musical judgement
                # anyone would make. It is said plainly in the status line, and
                # it lasts only until the next Play.
                if self._mix is not None and not self._mix.overloaded:
                    if self._q.qsize() < LOOKAHEAD_BLOCKS // 4:
                        low += 1
                        if low > 40:
                            self._mix.overloaded = True
                            self.bypassed = True
                    else:
                        low = 0
                blk = self._render_one()
            except Exception:
                # A render that raises must not take the sound with it: hand
                # the sink silence and stop, rather than wedging the thread in
                # a loop that raises on every block.
                self._stop.set()
                break
            while not self._stop.is_set():
                try:
                    self._q.put(blk, timeout=0.2)
                    break
                except queue.Full:
                    continue

    def _need_data(self, src, _length):
        """The sink wants more. Runs on the GStreamer streaming thread."""
        try:
            data, pl, pr, tp, at = self._q.get_nowait()
            self._peaks = (pl, pr)
            self._track_peaks = tp
            # THE PIPELINE'S CLOCK ONLY EVER GOES FORWARD, and the song's
            # position does not: a loop sends it back to the top every few
            # bars. Note where the two stopped agreeing, so position() can put
            # the playhead back where the sound actually is instead of running
            # it off the end of the arrangement.
            if at != self._next_song:
                self._map.append((self._pushed, at))
                del self._map[:-64]
            self._next_song = at + nbsynth.BLOCK
        except queue.Empty:
            data = self._silence
            self._peaks = (0.0, 0.0)
            self.underruns += 1
        self._pushed += nbsynth.BLOCK
        if self._t0 is None:
            self._t0 = time.monotonic()
        try:
            src.emit("push-buffer", Gst.Buffer.new_wrapped(data))
        except Exception:
            pass

    def _on_bus_error(self, _bus, _msg):
        # the sink can fail asynchronously (no audio device) — drop to silence
        # and let the status line surface the neutral note
        self._teardown()
        self.failed = True

    def stop(self):
        self._stop.set()
        t = self._thread
        self._thread = None
        if t is not None and t.is_alive():
            # Never wait long on the GTK thread: the render thread parks on a
            # queue put with its own timeout, so it is always about to notice.
            t.join(timeout=0.5)
        self._teardown()

    def _teardown(self):
        try:
            if self._pipe is not None:
                self._pipe.set_state(Gst.State.NULL)
        except Exception:
            pass
        self._pipe = None
        self._src = None
        self.available = False
        self._t0 = None
        if self._mix is not None:
            try:
                self._mix.close()
            except Exception:
                pass
            self._mix = None
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass
        self._peaks = (0.0, 0.0)
        self._track_peaks = []

    def shutdown(self):
        self.stop()

    # -- what the window asks it -----------------------------------------
    def update(self, song):
        """Adopt an edited arrangement without stopping.

        A note drawn, a track muted or a fader moved while the transport runs
        is heard as soon as the bank turns over — it does not wait for the next
        Play, and it does not cut the sound to get there."""
        if self._mix is None:
            return
        with self._lock:
            self._pending = song

    def position(self):
        """Where the sound actually is, in song seconds.

        The pipeline's own clock is the truth — it counts what the sink has
        PLAYED, not what we have handed it, and those differ by the whole
        lookahead. It is measured in frames since the pipeline started, though,
        which is only the same thing as a position in the song while the song
        runs straight through; the map above translates it back across every
        loop. Falls back to a wall clock on a pipeline that will not answer a
        position query."""
        played = None
        if self._pipe is not None:
            try:
                ok, ns = self._pipe.query_position(Gst.Format.TIME)
                if ok and ns >= 0:
                    played = int(ns * SR / 1e9)
            except Exception:
                played = None
        if played is None:
            if self._t0 is None:
                return self._start_at
            played = int((time.monotonic() - self._t0) * SR)
        song = None
        for pushed, at in reversed(self._map):
            if pushed <= played:
                song = at + (played - pushed)
                break
        if song is None:
            song = self._map[0][1] + played
        return song / float(SR)

    def peaks(self):
        return self._peaks

    def track_peaks(self):
        return self._track_peaks


_TAKE_LEN = {}


def take_length(path):
    """How long a take's file is, in seconds (cached, 0.0 if unreadable)."""
    try:
        st = os.stat(path)
        key = (path, st.st_size, int(st.st_mtime))
    except OSError:
        return 0.0
    got = _TAKE_LEN.get(key)
    if got is None:
        info = nbsynth.wav_info(path)
        got = info[0] if info else 0.0
        if len(_TAKE_LEN) > 96:
            _TAKE_LEN.clear()
        _TAKE_LEN[key] = got
    return got


def clip_take_len(c):
    """How many seconds of recording a clip has to draw on, or None.

    None means "no limit known" — a clip whose take has gone missing, or one
    that never had a take. Trimming has to allow those to be dragged anywhere
    rather than refusing to move at all."""
    wav = (c or {}).get("wav")
    if not wav:
        return None
    got = take_length(wav)
    return got if got > 0 else None


class WaveEdit(Gtk.DrawingArea):
    """One recorded take, and the part of it a clip uses.

    THE WHOLE RECORDING IS DRAWN, not just the part in use, because trimming is
    the commonest thing anyone does to a take and it is impossible to judge
    against a picture that has already been cut. The stretch the clip plays is
    solid; everything either side of it is still there, faint, and one drag
    away from coming back — nothing recorded is ever thrown out by trimming.

    The bar lines are drawn over the used stretch at the position they fall in
    the ARRANGEMENT, so whether a take's downbeat lands on a bar — the one
    thing that decides whether a loop will work — can be seen rather than
    guessed at."""
    EDGE = 9          # pixels either side of a boundary that grab it
    PAD = 26          # top strip, where the fades are drawn
    # A clip that uses the whole take puts its handles at the very edges of the
    # widget, where half of each one is off-screen and neither can be grabbed.
    # The take is drawn inset by this much so both ends are always reachable.
    MARGIN = 14

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                        | Gdk.EventMask.BUTTON_RELEASE_MASK
                        | Gdk.EventMask.POINTER_MOTION_MASK
                        | Gdk.EventMask.BUTTON1_MOTION_MASK)
        self.connect("draw", self._draw)
        self.connect("button-press-event", self._press)
        self.connect("motion-notify-event", self._motion)
        self.connect("button-release-event", self._release)
        self.set_tooltip_text(
            _t("Drag either end of the highlighted part to trim the take  ·  "
               "click inside it to move the playhead"))
        self.drag = None        # ("in"|"out", grab offset in seconds)
        self.hover = None

    # -- geometry --------------------------------------------------------
    def _take(self):
        """(clip, path, whole-take seconds) or None."""
        c = self.app.sel_clip()
        if c is None or not c.get("wav"):
            return None
        return c, c["wav"], take_length(c["wav"])

    def _x_of(self, t, W, dur):
        m = self.MARGIN
        return (t / dur) * (W - 2 * m) + m if dur > 0 else m

    def _t_at(self, x, W, dur):
        m = self.MARGIN
        return max(0.0, min(dur, (x - m) / max(1, W - 2 * m) * dur))

    def _grab(self, x, W, dur, c):
        """Which boundary the pointer is on, if any."""
        xi = self._x_of(c["off"], W, dur)
        xo = self._x_of(c["off"] + (c["e"] - c["s"]), W, dur)
        if abs(x - xi) <= self.EDGE:
            return "in"
        if abs(x - xo) <= self.EDGE:
            return "out"
        return None

    # -- gestures --------------------------------------------------------
    def _press(self, _w, ev):
        got = self._take()
        if got is None or self.app.transport == "rec":
            return True
        c, _path, dur = got
        W = self.get_allocated_width()
        if dur <= 0:
            return True
        if ev.button != 1:
            return True
        where = self._grab(ev.x, W, dur, c)
        if where:
            self.app.remember_edit("Trim")
            self.drag = (where, self._t_at(ev.x, W, dur))
            return True
        # inside the used part: put the playhead there
        t = self._t_at(ev.x, W, dur)
        at = c["s"] + (t - c["off"])
        if c["s"] <= at <= c["e"]:
            self.app.pos = at
            self.app.refresh()
        return True

    def _motion(self, _w, ev):
        got = self._take()
        if got is None:
            return False
        c, _path, dur = got
        W = self.get_allocated_width()
        if self.drag is None:
            h = self._grab(ev.x, W, dur, c)
            if h != self.hover:
                self.hover = h
                self.queue_draw()
            return False
        where, _grab = self.drag
        t = self._t_at(ev.x, W, dur)
        span = c["e"] - c["s"]
        if where == "in":
            # Trimming the START moves the clip in the arrangement by the same
            # amount it moves into the take, so the audio that is left does not
            # slide in time — cutting a count-in off the front must not move
            # the take that follows it.
            t = max(0.0, min(c["off"] + span - 0.05, t))
            d = t - c["off"]
            if abs(d) > 1e-6:
                c["off"] += d
                c["s"] += d
        else:
            t = max(c["off"] + 0.05, min(dur, t))
            c["e"] = c["s"] + (t - c["off"])
        self.queue_draw()
        return True

    def _release(self, _w, _ev):
        if self.drag is None:
            return False
        self.drag = None
        self.app.clip_changed()
        return True

    # -- painting --------------------------------------------------------
    def _draw(self, w, cr):
        a = w.get_allocation()
        W, H = a.width, a.height
        if W < 8 or H < 8:
            return False
        cr.set_source_rgb(*SURF)
        cr.rectangle(0, 0, W, H)
        cr.fill()
        got = self._take()
        if got is None:
            return False
        c, path, dur = got
        if dur <= 0:
            return False
        peaks = wave_peaks(path, max(200, W))
        top = self.PAD
        mid = top + (H - top) / 2.0
        half = (H - top) / 2.0 - 8
        xi = self._x_of(c["off"], W, dur)
        xo = self._x_of(c["off"] + (c["e"] - c["s"]), W, dur)

        # the part of the take the clip uses, as a panel behind the wave
        cr.set_source_rgb(0xF4 / 255, 0xF1 / 255, 0xE7 / 255)
        cr.rectangle(xi, top, max(1, xo - xi), H - top)
        cr.fill()
        # centre line
        cr.set_source_rgb(*CENTER)
        cr.rectangle(0, int(mid), W, 1)
        cr.fill()
        # bar lines, at the position they fall in the ARRANGEMENT
        spb = self.app.sec_per_bar()
        if spb > 0:
            k = math.floor(c["s"] / spb)
            while True:
                at = k * spb
                t = c["off"] + (at - c["s"])
                k += 1
                if t > c["off"] + (c["e"] - c["s"]) + spb:
                    break
                if t < c["off"] - 1e-9:
                    continue
                x = self._x_of(t, W, dur)
                if x > xo + 1:
                    break
                cr.set_source_rgb(*HAIR)
                cr.rectangle(round(x), top, 1, H - top)
                cr.fill()

        # the wave itself: solid inside the clip, faint outside it
        if peaks:
            n = len(peaks)
            m = self.MARGIN
            g = max(0.05, min(4.0, c.get("gain", 1.0)))
            for px in range(m, W - m):
                lo = int((px - m) * n / max(1, W - 2 * m))
                hi = max(lo + 1, int((px - m + 1) * n / max(1, W - 2 * m)))
                v = max(peaks[lo:hi]) if hi <= n else peaks[min(lo, n - 1)]
                hgt = max(0.6, min(1.0, v * g) * half)
                inside = xi - 1 <= px <= xo + 1
                if inside:
                    e = self._fade_at(c, self._t_at(px, W, dur))
                    hgt *= e
                    cr.set_source_rgba(*INK, 0.72)
                else:
                    cr.set_source_rgba(*FAINT, 0.5)
                cr.rectangle(px, mid - hgt, 1, hgt * 2)
                cr.fill()

        # the fade ramps, drawn across the top strip so their length is legible
        self._fade_marks(cr, c, W, dur, top, xi, xo)

        # the trim handles
        for x, name in ((xi, "in"), (xo, "out")):
            hot = self.hover == name or (self.drag and self.drag[0] == name)
            cr.set_source_rgb(*(RED if hot else INK))
            cr.rectangle(round(x) - 1, top, 3, H - top)
            cr.fill()
            cr.rectangle(round(x) - (7 if name == "in" else 0), top, 7, 9)
            cr.fill()

        # playhead, when the transport is inside this clip
        if self.app.transport in ("play", "rec") \
                and c["s"] <= self.app.pos <= c["e"]:
            t = c["off"] + (self.app.pos - c["s"])
            cr.set_source_rgb(*RED)
            cr.rectangle(round(self._x_of(t, W, dur)) - 1, top, 2, H - top)
            cr.fill()
        return False

    @staticmethod
    def _fade_at(c, t):
        """The fade envelope at take-time t, 0..1."""
        p = t - c["off"]
        span = c["e"] - c["s"]
        e = 1.0
        fin = c.get("fin", 0.0)
        fout = c.get("fout", 0.0)
        if fin > 0 and p < fin:
            e = max(0.0, p / fin)
        if fout > 0 and p > span - fout:
            e *= max(0.0, (span - p) / fout)
        return max(0.0, min(1.0, e))

    def _fade_marks(self, cr, c, W, dur, top, xi, xo):
        cr.set_source_rgba(*INK, 0.5)
        cr.set_line_width(1)
        fin = c.get("fin", 0.0)
        fout = c.get("fout", 0.0)
        if fin > 0.001:
            x2 = self._x_of(c["off"] + fin, W, dur)
            cr.move_to(xi, top - 2)
            cr.line_to(min(x2, xo), 4)
            cr.stroke()
        if fout > 0.001:
            span = c["e"] - c["s"]
            x1 = self._x_of(c["off"] + span - fout, W, dur)
            cr.move_to(max(x1, xi), 4)
            cr.line_to(xo, top - 2)
            cr.stroke()


class Sequencer(nbapp.AppWindow):
    app_name = "Sequencer"
    menus = ("File", "Edit", "View", "Transport", "Track", "Input")

    def __init__(self):
        super().__init__()
        self._install_css()

        # ---- state ----
        # Every deferred sink checks this because source_remove cannot recall a
        # callback that the main loop has already dispatched during teardown.
        self._closed = False
        self.transport = "stop"
        self.pos = 0.0
        self.rec_start = None
        self.tick = 0
        self._path = None       # current project file (File ▸ Save), or None
        self._loading = False   # guards value-changed handlers during sync
        self._save_timer = None    # pending debounced autosave, or None
        self._extra = {}           # forward-compatible top-level project keys
        self._saved_timer = None   # transient 'Saved HH:MM' restore, or None
        self._prompt_layer = None  # open confirm-card overlay, if any
        self._runner_id = None     # 100ms transport tick source, when engaged
        self._undo_stack = []      # arrangement snapshots, newest last
        self._redo_stack = []
        # what each snapshot would take back, for the Edit menu ("Undo Clear
        # All Takes"). None = an edit that needs no name (drawing a part).
        self._undo_names = []
        self._redo_names = []
        self.history = StackHistory(self)
        self._rendered = {}        # last-rendered values, so refresh() only
        #                            rewrites a widget when its value changed
        # the sound engine: nbsynth renders, AudioOut streams (see the classes)
        self.engine = AudioOut()
        self._free_pos = 0.0       # playhead while the transport runs silently
        self._free_t0 = None
        self._vu_hold = [0.0] * TRACKS   # meter ballistics (see _vu_level)
        self._preroll = 0.0        # count-in ahead of a take, in seconds
        # capture (Mic tracks) — see Recorder / capture_devices
        self.recorder = Recorder()
        self._cap_device = None      # ALSA name; None = first available
        # Which clip the editor is showing, held as (track index, the clip
        # OBJECT). Not an index into the clip list: clips are added, removed and
        # merged constantly, and an index would quietly come to mean a
        # different clip — or none — after any of that.
        self.sel = None
        self.view = "arrange"
        self._export = None          # running export thread, or None
        self._clipboard = None       # a clip copied with Ctrl+C, or None
        self._hs_sync = False        # guards the h-scrollbar against itself
        self._hs_last = None         # the value WE last wrote to it (see below)
        # HOW THE TAPE IS BEING LOOKED AT. Not part of the project: it is a
        # view, like a scroll position, and restoring somebody's zoom from a
        # file they opened on a different screen helps nobody.
        self.zoom = ZOOM_FIT         # x1 = the whole arrangement across a lane
        self.view_start = 0.0        # seconds at the left edge of a lane
        self.tool = TOOL_SELECT
        # the view's travel (see _animate_view): the running Scalar, whether it
        # is in flight, and where it is HEADED — a second wheel notch aims on
        # from the target rather than from wherever the spring has got to.
        self._view_anim = None
        self._view_moving = False
        self._view_target_start = None
        self._view_target_zoom = None
        # tempo, metronome, length, snap, master and the 8 tracks
        # (name/arm/mute/solo/gain/clips) are restored from disk; a fresh
        # install yields the empty default tape.
        self._load_state()

        self.content.pack_start(self._transport_bar(), False, False, 0)
        self.content.pack_start(self._viewbar(), False, False, 0)
        self.content.pack_start(self._view_stack(), True, True, 0)
        self.content.pack_start(self._status_bar(), False, False, 0)

        self.connect("key-press-event", self._on_space)
        # flush the mix on close so the last tweak or take is never lost
        self.connect("destroy", self._on_destroy)
        # the 100ms transport tick is armed lazily (only while play/rec/ff/rew is
        # engaged) and drops itself on stop — see _ensure_runner / _runner. The
        # app ships stopped, so nothing is scheduled until the user presses go.
        self._update_length_btn()
        self._update_proj()
        self._flash_take_damage()
        self._set_tool(self.tool)
        self._update_snap_btn()
        self._update_zoom_btn()
        self.refresh()

    # ================= persistence =================
    def _default_tracks(self):
        """The ships-empty 8-track studio: no takes, nothing armed.

        Eight identical numbered lanes. They used to arrive named for the parts
        of a bedroom-pop record — Drums, Bass, Guitar, Keys, Vocal — which
        named somebody else's song: every one of them is the same thing, a lane
        that records what is played into it, and a name typed over a suggestion
        is more work than a name typed into a number."""
        rev, dly = nbsynth.DEFAULT_SENDS
        return [{"name": "Track %d" % (i + 1),
                 "armed": False, "muted": False, "solo": False,
                 "gain": GAIN_UNITY, "pan": 0, "clips": [],
                 "low": 0, "high": 0, "comp": 0,
                 "rev": int(round(rev * SEND_MAX)),
                 "dly": int(round(dly * SEND_MAX))}
                for i in range(TRACKS)]

    def _base_track(self, i):
        """The neutral fallback a SAVED track falls back to, field by field."""
        rev, dly = nbsynth.DEFAULT_SENDS
        return {"name": "Track %d" % (i + 1),
                "armed": False, "muted": False, "solo": False,
                "gain": GAIN_UNITY, "pan": 0,
                "rev": int(round(rev * SEND_MAX)),
                "dly": int(round(dly * SEND_MAX)),
                "low": 0, "high": 0, "comp": 0, "clips": []}

    def _norm_track(self, i, t):
        """Coerce one saved track dict into the full in-memory shape."""
        base = self._base_track(i)
        if not isinstance(t, dict):
            return self._default_tracks()[i]
        name = str(t.get("name") or base["name"]).strip()[:48] or base["name"]
        # A project written when a track had an INPUT slot carries one here.
        # The slot is gone — every track records — but for most of that slot's
        # life it was free text ("Rhythm gtr", "Bass DI"), and throwing it away
        # would silently unname the track. It becomes the track's NAME, which
        # is where a label of your own now belongs, unless the track already
        # has a name of its own. The three fixed values the slot ended up
        # holding are not labels and are dropped.
        raw = str(t.get("input") or "").strip()[:48]
        if (raw and raw.lower() not in ("mic", "drums 808", "drums 909")
                and (not name or name == base["name"]
                     or re.fullmatch(r"Track \d+", name))):
            name = raw
        clips = []
        for raw_clip in (t.get("clips") or []):
            c = clip_norm(raw_clip)
            if c is None:
                continue
            # Keep the reference even when the recording is unavailable. A
            # removable drive may return, and erasing the path on autosave
            # makes that recording impossible to reconnect. The clip remains
            # visible and silent while a status warning tells the truth.
            if c["wav"]:
                info = nbsynth.wav_info(c["wav"])
                if info is None or info[0] <= 0.0:
                    self._take_damage.append(c["wav"])
            c["s"] = max(0.0, min(self.length, c["s"]))
            c["e"] = max(0.0, min(self.length, c["e"]))
            if c["e"] - c["s"] <= 0.001:
                continue
            clips.append(c)
        # cap the name so a hand-edited file with a runaway string can't blow
        # out the fixed-width track head; whitespace-only falls back to default
        return {
            "name": name,
            "armed": bool(t.get("armed")),
            "muted": bool(t.get("muted")),
            "solo": bool(t.get("solo")),
            "gain": _clampi(t.get("gain"), 0, GAIN_MAX, GAIN_UNITY),
            "pan": _clampi(t.get("pan"), -PAN_MAX, PAN_MAX, 0),
            "rev": _clampi(t.get("rev"), 0, SEND_MAX, base["rev"]),
            "dly": _clampi(t.get("dly"), 0, SEND_MAX, base["dly"]),
            "low": _clampi(t.get("low"), 0, 100, 0),
            "high": _clampi(t.get("high"), 0, 100, 0),
            "comp": _clampi(t.get("comp"), 0, 100, 0),
            "clips": clips,
        }

    def _apply(self, data):
        """Load project state from a dict, clamping every field so a hand-edited
        or foreign file can't wedge launch. Missing keys fall back to defaults;
        an empty dict yields a fresh, empty project."""
        if not isinstance(data, dict):
            data = {}
        known = {"version", "bpm", "capture_device", "metronome", "countin",
                 "length", "master", "monitor", "snap", "loop_on", "loop_s",
                 "loop_e", "rev_size", "rev_mix", "dly_time", "dly_fb",
                 "dly_mix", "tape", "fx", "tracks"}
        self._extra = {k: v for k, v in data.items() if k not in known}
        self._take_damage = []
        self.length = _clampf(data.get("length"), 10.0, 600.0, DEFAULT_LEN)
        self.bpm = _clampi(data.get("bpm"), BPM_MIN, BPM_MAX, 120)
        cd = data.get("capture_device")
        self._cap_device = cd if isinstance(cd, str) and cd else None
        self.metronome = bool(data.get("metronome"))
        # On by default: a take that starts before the performer does is the
        # commonest way a first recording is wasted.
        self.countin = bool(data.get("countin", True))
        self.master = _clampi(data.get("master"), 0, GAIN_MAX, GAIN_UNITY)
        # Monitoring is ON unless it has been turned off. Not hearing what is
        # being recorded is the wrong default for a recorder: it is how a take
        # gets to the end before anyone finds out the microphone was pointed
        # the wrong way.
        self.monitor = bool(data.get("monitor", True))
        # ---- the grid ----
        # Kept with the project because how fine an edit has to be is a
        # property of the music, not of the sitting: a four-on-the-floor demo
        # is edited on bars and a spoken-word take on nothing at all. An
        # unrecognised value (a hand-edited file, or the old drum machine's
        # step size under the same name) falls back to bars rather than to
        # some grid nothing on screen is drawing.
        snap = _clampf(data.get("snap"), SNAP_FREE, 4.0, DEFAULT_SNAP)
        self.snap = snap if snap in [v for v, _n in SNAP_CHOICES] \
            else DEFAULT_SNAP
        # The loop: a stretch of the arrangement to go round and round while
        # something is played over it. Kept with the project, because which
        # part of a song is being worked on outlives one sitting.
        self.loop_on = bool(data.get("loop_on"))
        self.loop_s = _clampf(data.get("loop_s"), 0.0, 3600.0, 0.0)
        self.loop_e = _clampf(data.get("loop_e"), 0.0, 3600.0, 0.0)
        if self.loop_e - self.loop_s < 0.05:
            self.loop_s, self.loop_e, self.loop_on = 0.0, 0.0, False
        # ---- the master effects ----
        self.rev_size = _clampi(data.get("rev_size"), 0, 100, 70)
        self.rev_mix = _clampi(data.get("rev_mix"), 0, 100, 100)
        self.dly_time = _clampf(data.get("dly_time"), 0.0625, 4.0, 0.75)
        self.dly_fb = _clampi(data.get("dly_fb"), 0, 90, 32)
        self.dly_mix = _clampi(data.get("dly_mix"), 0, 100, 100)
        self.tape = _clampi(data.get("tape"), 0, 100, 0)
        self.fx = bool(data.get("fx", True))
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
        # A different project is a different tape, so the window onto it goes
        # back to the whole thing: a zoom kept from the last one would open
        # somebody else's song two bars wide, somewhere in the middle.
        self.zoom = ZOOM_FIT
        self.view_start = 0.0

    def _load_state(self):
        """Restore the last session from sequencer.json (session recovery)."""
        data = None
        parsed = False
        try:
            with open(CFG_FILE) as fh:
                data = json.load(fh)
            parsed = True
        except Exception:
            data = None
        if parsed and not self._is_project(data):
            self._quarantine()
        self._apply(data)

    @staticmethod
    def _is_project(data):
        """Whether `data` is recognisable as something this app wrote. Every
        store _serialize produces carries a non-empty "tracks" list (a project
        always has TRACKS of them, even when they are all empty), and every
        entry in it is a dict carrying both "name" and "clips", so this cannot
        misfire on our own file — including a brand-new empty project.

        CHECKING THE LIST ITSELF, not just that it IS a list, is the whole
        point. "tracks" holding eight strings (or numbers, or dicts of
        something else) passed the old test, so nothing was quarantined:
        _norm_track then rejected every entry and fell back to the eight
        default tracks, the close-time autosave wrote those over the store, and
        because a blank eight-track tape OUTWEIGHS the damaged store,
        nbapp._bak_would_shrink could not tell the .bak refresh on the SECOND
        open was a regression — so the only remaining copy of the take list was
        overwritten with blankness. Two opens, two closes, no user action."""
        if not (isinstance(data, dict) and isinstance(data.get("tracks"), list)
                and data["tracks"]):
            return False
        return all(isinstance(t, dict) and "name" in t and "clips" in t
                   for t in data["tracks"])

    def _quarantine(self):
        """Move a store this app cannot read as a project aside, under the same
        <name>.damaged-<timestamp> name nbapp.preserve_damaged uses.

        nbapp quarantines any store that fails to PARSE. It deliberately cannot
        cover this case: valid JSON of the wrong shape parses perfectly, and only
        this app knows the shape is not a project. Without this the app opens on
        its blank default and the close-time autosave writes that blankness over
        the file. nbapp's one .bak is not enough on its own here — a blank
        sequencer project is four named tracks, which outweighs the store it
        replaced, so the .bak guard cannot tell this is a regression (see
        nbapp._bak_would_shrink)."""
        try:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            dest = "%s.damaged-%s" % (CFG_FILE, stamp)
            n = 2
            while os.path.exists(dest):
                dest = "%s.damaged-%s-%d" % (CFG_FILE, stamp, n)
                n += 1
            os.replace(CFG_FILE, dest)
        except OSError:
            pass

    def _serialize(self):
        """The full project as a plain dict (autosave and File ▸ Save share it)."""
        out = dict(getattr(self, "_extra", {}))
        out.update({
            "version": 3,
            "bpm": self.bpm,
            "capture_device": self._cap_device,
            "metronome": self.metronome,
            "countin": self.countin,
            "length": self.length,
            "master": self.master,
            "monitor": self.monitor,
            "snap": self.snap,
            "loop_on": self.loop_on,
            "loop_s": self.loop_s,
            "loop_e": self.loop_e,
            "rev_size": self.rev_size,
            "rev_mix": self.rev_mix,
            "dly_time": self.dly_time,
            "dly_fb": self.dly_fb,
            "dly_mix": self.dly_mix,
            "tape": self.tape,
            "fx": self.fx,
            "tracks": [
                {"name": tk["name"],
                 "armed": tk["armed"], "muted": tk["muted"],
                 "solo": tk["solo"], "gain": tk["gain"], "pan": tk["pan"],
                 "rev": tk["rev"], "dly": tk["dly"], "low": tk["low"],
                 "high": tk["high"], "comp": tk["comp"],
                 # A clip is written as the dict it is. Values that are at
                 # their default are left out, so a file stays readable by eye.
                 "clips": [_clip_json(c) for c in tk["clips"]]}
                for tk in self.tracks
            ],
        })
        return out

    def _song(self):
        """The arrangement in the shape nbsynth renders (see its docstring).

        One place translates this app's stored integers — a gain of 0..150, a
        pan of -100..100, a send of 0..100 — into the engine's plain ratios, so
        playback, the meters and the exported file can never disagree about
        what the mix is."""
        return {
            "bpm": self.bpm,
            "length": self.length,
            "master": self.master / 100.0,
            "metronome": self.metronome,
            "tape": self.tape / 100.0,
            "fx": self.fx,
            "loop": ([self.loop_s, self.loop_e]
                     if (self.loop_on and self.loop_e - self.loop_s > 0.05)
                     else None),
            "reverb": {"mix": self.rev_mix / 100.0,
                       "size": self.rev_size / 100.0},
            "delay": {"mix": self.dly_mix / 100.0, "time": self.dly_time,
                      "feedback": self.dly_fb / 100.0},
            "tracks": [
                {"gain": tk["gain"] / 100.0,
                 "pan": tk["pan"] / float(PAN_MAX),
                 "mute": tk["muted"], "solo": tk["solo"],
                 "rev": tk["rev"] / 100.0, "dly": tk["dly"] / 100.0,
                 "low": tk["low"] / 100.0, "high": tk["high"] / 100.0,
                 "comp": tk["comp"] / 100.0,
                 "clips": tk["clips"]}
                for tk in self.tracks
            ],
        }

    def _save_soon(self):
        """Coalesce rapid mutations (a slider drag fires value-changed on every
        frame) into one deferred disk write, so we never serialize + write JSON
        on the GTK main loop per frame. Flushed by _save() / stop / close."""
        if self._closed or self._save_timer is not None:
            return
        self._save_timer = GLib.timeout_add(600, self._save_timer_fire)

    def _save_timer_fire(self):
        self._save_timer = None
        if self._closed:
            return False
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
        except Exception as exc:
            self._flash(nbapp.save_failure_reason(exc, CFG_FILE))

    # ================= undo / redo =================
    # A take costs the user real time at the microphone, and until now one click
    # on a track's bin — or Clear All Takes, or shortening the tape — threw it
    # away for good. Every one of those is now a step back.
    def _arrangement(self):
        """The complete project plus transient editing position.

        New and Open replace every project control, while the clip-destructive
        actions need the same snapshot shape so all history steps interoperate.
        Selection and playhead state are transient and therefore travel beside
        the serialized project rather than inside its on-disk format."""
        sel = None
        if self.sel:
            try:
                sel = (self.sel[0], self.tracks[self.sel[0]]["clips"].index(
                       self.sel[1]))
            except (ValueError, IndexError):
                pass
        return {
            # New and Open replace more than clips: tempo, mix, grid and every
            # per-track control are user-visible project state too.
            "project": copy.deepcopy(self._serialize()),
            # the file the arrangement belongs to travels with it, so undoing a
            # New or an Open cannot leave Save pointed at the wrong project
            "path": self._path,
            "sel": sel, "pos": self.pos, "rec_start": self.rec_start,
        }

    def _restore_arrangement(self, snap):
        self._apply(copy.deepcopy(snap["project"]))
        self._path = snap["path"]
        self.pos = min(snap["pos"], self.length)
        rec_start = snap["rec_start"]
        self.rec_start = (min(rec_start, self.length)
                          if rec_start is not None else None)
        self.sel = None
        if snap["sel"] is not None:
            ti, ci = snap["sel"]
            if 0 <= ti < len(self.tracks) and 0 <= ci < len(self.tracks[ti]["clips"]):
                self.sel = (ti, self.tracks[ti]["clips"][ci])

    def _remember(self, name=None):
        """Bank the arrangement so the edit about to happen can be undone. Any
        fresh edit makes the redone future unreachable, so the Redo trail goes.

        `name` is the menu wording of the edit being made, which the Edit menu
        shows ("Undo Clear All Takes"); None for one that needs no name."""
        self._undo_stack.append(self._arrangement())
        self._undo_names.append(name)
        if len(self._undo_stack) > UNDO_DEPTH:
            self._undo_stack.pop(0)
            self._undo_names.pop(0)
        self._redo_stack = []
        self._redo_names = []

    def _step_history(self, take, give, take_names, give_names):
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
        give_names.append(take_names.pop() if take_names else None)
        if len(give) > UNDO_DEPTH:
            give.pop(0)
            give_names.pop(0)
        self._restore_arrangement(take.pop())
        self._sync_controls()       # names and switches back into the heads
        self._update_length_btn()
        self._update_proj()
        self._save()
        self.refresh()
        return True

    def _undo(self):
        self._step_history(self._undo_stack, self._redo_stack,
                           self._undo_names, self._redo_names)

    def _redo(self):
        self._step_history(self._redo_stack, self._undo_stack,
                           self._redo_names, self._undo_names)

    def _on_destroy(self, *_):
        # never leave an arecord child, a render thread or a pipeline holding
        # the sound device past the window
        if self._closed:
            return False
        self._closed = True
        try:
            self.recorder.stop()
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
            self._last_save_failure = None
            return True
        except Exception as exc:
            self._last_save_failure = nbapp.save_failure_reason(exc, path)
            return False

    def _open_file(self, path):
        """Load a project file, then push it into every control. True on ok."""
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            self._flash(_t("Couldn't open that project"))
            return False
        # Every app writes JSON into the shared Documents folder, so a readable
        # dict is not proof this is ours. Verify the Sequencer project shape
        # BEFORE mutating anything — adopting a foreign file would wipe the
        # tracks, overwrite session recovery with the empty default, and let a
        # later Save clobber that file. On mismatch flash and change nothing.
        # ONE definition of "is this ours" (_is_project), shared with session
        # recovery. This used to accept any dict with a "tracks" LIST in it,
        # whatever the list held, so a foreign or damaged file was adopted: the
        # tape went blank, _save() wrote that blankness over session recovery,
        # and _path pointed at the user's file so the next Save clobbered it too.
        if not self._is_project(data):
            self._flash(_t("That file isn't a Sequencer project"))
            return False
        if self.transport == "rec":
            self._stop_transport()
        self._remember("Open")   # opening the wrong project is one step back
        self._apply(data)
        self._path = path
        self._sync_controls()
        self._update_proj()
        self._flash_take_damage()
        self._save()            # snapshot recovery adopts the opened project
        return True

    def _file_new(self):
        """Blank project (empty tape, defaults). Confirms first when there are
        recorded takes a new project would discard; the file on disk is left
        alone either way."""
        self._do_file_new()

    def _do_file_new(self):
        if self.transport == "rec":
            self._stop_transport()
        self._remember("New Project")
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
        self._open_file(path)

    def _file_save(self):
        """Write to the current project file; prompt via Save As if none."""
        if not self._path:
            return self._file_save_as()
        if self._write_file(self._path):
            self._update_proj()
            self._flash_saved()
        else:
            self._flash(getattr(self, "_last_save_failure", None)
                        or _t("Couldn't save the project"))

    def _file_save_as(self):
        path = self._choose_file(save=True)
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += ".json"
        old_path = self._path
        self._path = path
        if self._write_file(path):
            self._update_proj()
            self._flash_saved()
        else:
            self._path = old_path
            self._flash(getattr(self, "_last_save_failure", None)
                        or _t("Couldn't save the project"))

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

    def _flash_take_damage(self):
        """Tell the user when saved clip geometry outlives its take files."""
        n = len(getattr(self, "_take_damage", []))
        if n:
            self._flash(_t("%d take file is unavailable; its clip remains in place")
                        % n if n == 1 else
                        _t("%d take files are unavailable; their clips remain in place")
                        % n)

    def _flash_saved(self):
        """Confirm an explicit File ▸ Save with a green-dot 'Saved HH:MM' chip
        (the write model the other File-Save apps share), then restore the plain
        project path after a moment. Session recovery autosaves continuously;
        this reassures the user the named *project file* was actually written."""
        if self._closed:
            return
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
        if self._closed:
            return False
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
                # states the situation instead of reciting a menu path (and is
                # translated here: this is only ever applied with set_text)
                self.proj_lbl.set_text(_t("Not saved to a file"))
        except Exception:
            pass

    def _sync_controls(self):
        """Push loaded state into every widget (used by New / Open). Guarded so
        the value-changed handlers don't fight the load or re-fire autosaves."""
        self._loading = True
        try:
            self.master_scale.set_value(self.master)
            self.master_fader.set_value(self.master)
            self.bpm_scale.set_value(self.bpm)
            self.rev_size_s.set_value(self.rev_size)
            self.rev_mix_s.set_value(self.rev_mix)
            self.dly_fb_s.set_value(self.dly_fb)
            self.tape_s.set_value(self.tape)
            for i, tk in enumerate(self.tracks):
                tw = self.track_widgets[i]
                tw["gain"].set_value(tk["gain"])
                if tw["name"].get_text() != tk["name"]:
                    tw["name"].set_text(tk["name"])   # _loading gates the handler
                tw["name"].set_tooltip_text(tk["name"])
                st = self.strips[i]
                st["name"].set_text(tk["name"])
                st["gain"].set_value(tk["gain"])
                st["pan"].set_value(tk["pan"])
                st["rev"].set_value(tk["rev"])
                st["dly"].set_value(tk["dly"])
                st["low"].set_value(tk["low"])
                st["high"].set_value(tk["high"])
                st["comp"].set_value(tk["comp"])
        finally:
            self._loading = False
        self._update_length_btn()
        self._update_snap_btn()
        self._after_view_change()
        self._validate_sel()
        self._sync_editor()
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
        # Every transport control is a bare symbol. RTZ below already spells
        # itself out for anyone who has never used a deck; these did not.
        specs = [("rew", "rew", self._on_rew, "Rewind"),
                 ("ff", "ff", self._on_ff, "Fast forward"),
                 ("stop", "stopsq", self._on_stop, "Stop"),
                 ("play", "play", self._on_play, "Play")]
        for key, icon, cb, tip in specs:
            b = Gtk.Button()
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("tbtn")
            b.set_tooltip_text(tip)
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
        rec.set_tooltip_text(_t("Record"))
        # the rec dot sits on the rec button, not the rail — its opaque backing
        # tracks the button surface (base #FCFBF8 → signage red when armed)
        self.recdot = Dot(14, bg=SURF)
        self.recdot.set_color(RED)
        rec.add(self.recdot)
        rec.connect("clicked", self._on_rec)
        self.tbuttons["rec"] = (rec, None, None)
        btns.pack_start(rec, False, False, 0)
        bar.pack_start(btns, False, False, 0)

        # MONITOR belongs beside the record button, not out on the right of
        # the deck where the mix controls live. The deck SCROLLS on a 1024-wide
        # panel — that is what keeps every control reachable — and out there
        # this was the first thing off the edge. It cannot be: it is the
        # difference between playing along to a record and playing into a void,
        # and the one moment it has to be turned OFF in a hurry is a microphone
        # howling into the speakers it is being monitored through.
        pbox = Gtk.Box(spacing=8)
        self.mon_btn = Gtk.Button(label=_t("MONITOR"))
        self.mon_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.mon_btn.get_style_context().add_class("metrobtn")
        self.mon_btn.set_tooltip_text(
            _t("Hear the input through the speakers while recording. Turn "
               "this off if the microphone can hear them too."))
        self.mon_btn.connect("clicked", self._toggle_monitor)
        pbox.pack_start(self.mon_btn, False, False, 0)
        self.mon_dot = Dot(11)
        self.mon_dot.set_color(VU_OFF)
        pbox.pack_start(self.mon_dot, False, False, 0)
        # The input's own meter, live from the pump thread the moment the take
        # starts: a level nobody can see until the take is over is a level set
        # by guessing.
        self.in_vu = VU(10)
        self.in_vu.set_tooltip_text(_t("Level coming in"))
        pbox.pack_start(self.in_vu, False, False, 0)
        bar.pack_start(pbox, False, False, 0)

        # LOOP sits with the transport rather than with the tempo controls: it
        # decides how the transport MOVES, and the cluster over on the right
        # scrolls off the edge of a 1024-wide panel, which is not somewhere to
        # keep a control that has to be reachable while something is playing.
        self.loop_btn = Gtk.Button(label=_t("LOOP"))
        self.loop_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.loop_btn.get_style_context().add_class("metrobtn")
        self.loop_btn.set_tooltip_text(
            _t("Play the looped bars over and over — drag across the ruler "
               "to choose them"))
        self.loop_btn.connect("clicked", self._toggle_loop)
        bar.pack_start(self.loop_btn, False, False, 0)

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
        # There was a "BAR 1·1" readout here. The counter to the left already
        # begins with the bar and the beat — the same two numbers, in a bigger
        # face — and on a 1024-wide panel it was pushing the LOOP control off
        # the edge of the deck to say them twice.
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

        sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep.get_style_context().add_class("vsep")
        bar.pack_start(sep, False, False, 0)

        # master
        mbox = Gtk.Box(spacing=10)
        mbox.pack_start(self._caplabel("Master"), False, False, 0)
        self.master_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, GAIN_MAX, 1)
        self.master_scale.set_draw_value(False)
        # A mark at unity, so the point where the fader neither cuts nor boosts
        # is visible rather than something to be found by ear.
        self.master_scale.add_mark(GAIN_UNITY, Gtk.PositionType.BOTTOM, None)
        self.master_scale.set_value(self.master)
        self.master_scale.set_size_request(130, -1)
        self.master_scale.connect("value-changed", self._on_master)
        mbox.pack_start(self.master_scale, False, False, 0)
        # numeric readout (percentage + dB), kept live in refresh() even when
        # the transport is stopped so the fader always shows where it's set
        self.master_lbl = Gtk.Label(label="%d%%" % GAIN_UNITY)
        self.master_lbl.get_style_context().add_class("smallnum")
        # "150% · +4 dB" is the widest this ever reads; at 80px the percentage
        # ran out of its label and over the fader beside it.
        self.master_lbl.set_size_request(104, -1)
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

    # ================= views =================
    VIEWS = (("arrange", "ARRANGE"), ("edit", "EDIT"), ("mix", "MIX"))

    def _viewbar(self):
        """The three views, and what the middle one is currently showing.

        A row of its own rather than another control on the deck: it is the
        window's primary navigation, and the deck already scrolls sideways on a
        narrow panel — navigation that can scroll off the edge is navigation
        nobody finds."""
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        bar.get_style_context().add_class("viewbar")
        self.view_btns = {}
        seg = Gtk.Box(spacing=0)
        seg.get_style_context().add_class("seg")
        tips = {"arrange": _t("Clips on the timeline"),
                "edit": _t("Notes in the selected clip"),
                "mix": _t("Levels, pan, sends and the master effects")}
        for key, label in self.VIEWS:
            b = Gtk.Button(label=_t(label))
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("segbtn")
            b.set_tooltip_text(tips[key])
            b.connect("clicked", lambda _b, k=key: self._set_view(k))
            self.view_btns[key] = b
            seg.pack_start(b, False, False, 0)
        bar.pack_start(seg, False, False, 0)
        self.view_ctx = Gtk.Label(label="")
        self.view_ctx.get_style_context().add_class("viewctx")
        self.view_ctx.set_xalign(0)
        self.view_ctx.set_ellipsize(Pango.EllipsizeMode.END)
        self.view_ctx_extra = ""
        bar.pack_start(self.view_ctx, True, True, 0)
        return bar

    def _view_stack(self):
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.stack.add_named(self._track_area(), "arrange")
        self.stack.add_named(self._editor_view(), "edit")
        self.stack.add_named(self._mixer_view(), "mix")
        # The pager is built once the three pages are named, and its order is
        # the order of the view buttons: Arrange -> Edit -> Mix reads forward,
        # Mix -> Arrange reads back, so the slide agrees with the segmented
        # control instead of each call site guessing a direction.
        self._view_pager = nbtransitions.PageSwitcher(
            self.stack, order=["arrange", "edit", "mix"],
            duration=nbtransitions.PAGE)
        # Arrange is where the app opens. Recording it as the target with an
        # explicit NONE means no page slides during construction, but the first
        # real trip to Edit still has somewhere to have come FROM and resolves
        # forward rather than falling back to a crossfade.
        self._view_pager.switch("arrange", direction=nbtransitions.NONE)
        return self.stack

    def _set_view(self, name):
        """Show one of the three views. EDIT needs a clip to be about."""
        if name == "edit" and self.sel_clip() is None:
            if not self._select_first_clip():
                self._flash(_t("Draw a clip on a lane, then open it here"))
                name = "arrange"
        self.view = name
        try:
            # A GtkStack will only switch to a child that is itself visible, and
            # a view built but never shown is not. Show it first, or asking for
            # a view before the window has been shown leaves the switcher lit
            # for one view and the screen on another.
            child = self.stack.get_child_by_name(name)
            if child is not None:
                child.show_all()
            self._view_pager.switch(name)
        except Exception:
            pass
        if name == "edit":
            self._sync_editor()
        self.refresh()

    # ---- the selected clip -------------------------------------------------
    def sel_clip(self):
        """The clip the editor is showing, or None."""
        if not self.sel:
            return None
        ti, c = self.sel
        if not (0 <= ti < len(self.tracks)):
            return None
        return c if any(x is c for x in self.tracks[ti]["clips"]) else None

    def sel_track(self):
        return self.tracks[self.sel[0]] if self.sel_clip() is not None else None

    def _validate_sel(self):
        """Drop a selection whose clip is no longer in the arrangement."""
        if self.sel_clip() is None:
            self.sel = None
            self._select_first_clip()

    def _select_first_clip(self):
        """Point the editor at the earliest clip in the arrangement. True if
        there was one."""
        best = None
        for ti, tk in enumerate(self.tracks):
            for c in tk["clips"]:
                if best is None or c["s"] < best[1]["s"]:
                    best = (ti, c)
        if best is None:
            return False
        self.sel = best
        return True

    def select_clip(self, ti, c):
        """Make `c` on track `ti` the clip the editor edits."""
        self.sel = (ti, c)
        self._sync_editor()
        self.refresh()

    def open_clip(self, ti, c):
        """Select a clip AND go to it — what a double-click on a lane means."""
        self.select_clip(ti, c)
        self._set_view("edit")

    def remember_edit(self, name=None):
        """Bank the arrangement before an edit (one step back per gesture)."""
        self._remember(name)

    def clip_changed(self):
        """A clip was moved, trimmed, cut or removed: save, redraw, re-render."""
        self._save_soon()
        self._engine_changed()
        self.refresh()

    # ================= the window onto the tape =================
    # ONE PAIR OF FUNCTIONS MAPS SECONDS TO PIXELS FOR THE WHOLE APP. The ruler
    # and eight lanes all ask these, so they cannot disagree about where a
    # second is — which is the only reason it is safe for the ruler to be a
    # separate widget from the lanes it numbers.
    def view_span(self):
        """Seconds of tape across the width of one lane."""
        return self.length / max(ZOOM_FIT, self.zoom)

    def time_at_px(self, x, W):
        """The moment under pixel `x` of a lane `W` wide."""
        if W <= 0:
            return 0.0
        return self.view_start + (float(x) / W) * self.view_span()

    def px_of_time(self, t, W):
        """Where moment `t` falls in a lane `W` wide. May be off either end."""
        span = self.view_span()
        if span <= 0 or W <= 0:
            return 0.0
        return (float(t) - self.view_start) / span * W

    def _clamp_view(self):
        span = self.view_span()
        self.view_start = max(0.0, min(max(0.0, self.length - span),
                                       self.view_start))

    # ---- the view TRAVELS to its new value, it does not jump to it ---------
    # nbmotion-inventory: app.zoom
    #
    # Every change of zoom or scroll position is a state change and animates,
    # with the house character (nbmotion.ARRIVE — a slight spring that peaks
    # ~5% past the target and settles onto it). Two details make it read as a
    # zoom rather than as a lurch:
    #
    #   * The zoom is interpolated in LOG space. Linearly, 1x -> 16x spends
    #     five sixths of the animation already zoomed in; geometrically every
    #     frame doubles-down by the same ratio, which is what "scaling" means
    #     and what the eye expects.
    #   * The ANCHOR is re-pinned every frame rather than interpolated. That is
    #     what makes it scale ABOUT THE POINTER: the moment under the pointer
    #     is held to the same pixel throughout, so the picture grows out from
    #     under the hand instead of sliding sideways underneath it.
    #
    # Reduced Motion, no frame clock and a zero token all need no branch here:
    # Scalar.animate_to lands on the target synchronously and fires on_done
    # before it returns (nbmotion's §F4 contract), so the still path is the
    # same code with the animation compressed to nothing.
    #
    # See Lane._draw for how this stays affordable on the software rasteriser:
    # a view change invalidates all eight cached lane surfaces, so while the
    # view is travelling the lanes BLIT the last good raster scaled to the
    # moving view and only re-render once, on arrival (Article F1).
    def _animate_view(self, z1, start1, anchor=None, frac=0.5,
                      duration=None):
        """Travel to (zoom z1, view_start start1). Returns True if anything
        will move.

        `anchor`/`frac` pin a moment to a fraction of the lane for the whole
        journey; pass anchor=None to interpolate view_start directly, which is
        what a scroll wants (nothing is being held still — the tape is moving
        past)."""
        z1 = max(ZOOM_FIT, min(ZOOM_MAX, float(z1)))
        z0, s0 = self.zoom, self.view_start
        span1 = self.length / max(ZOOM_FIT, z1)
        start1 = max(0.0, min(max(0.0, self.length - span1), start1))
        if abs(z1 - z0) < 1e-9 and abs(start1 - s0) < 1e-6:
            return False
        lz0, lz1 = math.log(max(ZOOM_FIT, z0)), math.log(z1)
        if anchor is not None:
            frac = max(0.0, min(1.0, frac))
        self._view_target_start = start1
        self._view_target_zoom = z1

        def on_frame(e):
            # `e` runs 0 -> 1 and, with a spring, a little past 1 and back.
            self.zoom = max(ZOOM_FIT,
                            min(ZOOM_MAX, math.exp(lz0 + (lz1 - lz0) * e)))
            if anchor is None:
                self.view_start = s0 + (start1 - s0) * e
            else:
                self.view_start = anchor - frac * self.view_span()
            self._clamp_view()
            self._view_moving = True
            self.sync_ruler()
            for lane in getattr(self, "lanes", []):
                lane.queue_draw()

        def on_done(_finished):
            # Land EXACTLY on the target whether the spring finished or was
            # retargeted mid-flight: an animation that leaves the zoom at
            # 15.97x has quietly made FIT unreachable.
            self._view_moving = False
            self._view_target_start = None
            self._view_target_zoom = None
            self.zoom, self.view_start = z1, start1
            self._clamp_view()
            self._after_view_change()

        self._view_anim = nbmotion.animate(
            self.stack, on_frame, 0.0, 1.0, duration or nbmotion.PAGE,
            easing=nbmotion.ARRIVE, on_done=on_done)
        return True

    def view_moving(self):
        """True while the view is travelling — the lanes read this to blit
        instead of re-rasterising, and follow_playhead to keep its hands off."""
        return bool(getattr(self, "_view_moving", False))

    def set_zoom(self, z, anchor=None, frac=0.5):
        """Zoom to `z`, keeping `anchor` at the same place across the lane.

        Zooming that does not hold something still is disorienting: the point
        of pressing + is to see MORE OF WHAT IS UNDER THE POINTER, not to be
        moved somewhere else in the song."""
        z = max(ZOOM_FIT, min(ZOOM_MAX, float(z)))
        if abs(z - self.zoom) < 1e-9:
            return False
        if anchor is None:
            anchor = self.pos
        frac = max(0.0, min(1.0, frac))
        span = self.length / max(ZOOM_FIT, z)
        return self._animate_view(z, anchor - frac * span, anchor, frac)

    def zoom_by(self, factor, anchor=None, frac=0.5):
        return self.set_zoom(self.zoom * factor, anchor, frac)

    def zoom_fit(self):
        """The whole arrangement, end to end. The way back from anywhere."""
        return self._animate_view(ZOOM_FIT, 0.0)

    def zoom_to(self, s, e):
        """Fill the lanes with the stretch from `s` to `e`, with a little air
        either side so what is being looked at is not flush to the edges."""
        span = max(0.05, e - s)
        pad = span * 0.06
        span += pad * 2
        return self._animate_view(
            max(ZOOM_FIT, min(ZOOM_MAX, self.length / span)), s - pad)

    def scroll_view(self, seconds):
        """Scroll the tape by `seconds`. Animated, and RETARGETING: a second
        wheel notch part-way through the first one aims further on from where
        the view has actually reached rather than restarting, which is what
        makes a flick of the wheel one continuous movement."""
        base = (self._view_target_start
                if self.view_moving() and self._view_target_start is not None
                else self.view_start)
        return self._animate_view(self.zoom, base + seconds,
                                  duration=nbmotion.SURFACE_IN)

    def _after_view_change(self):
        # Clamped here as well as at every caller, because the callers include
        # Undo and File ▸ Open, which change the LENGTH of the tape: a window
        # that was valid on a four-minute arrangement hangs off the end of a
        # one-minute one, and nothing else would have noticed.
        self._clamp_view()
        self._sync_hscroll()
        self.sync_ruler()
        for lane in getattr(self, "lanes", []):
            lane.queue_draw()
        self._update_zoom_btn()

    def sync_ruler(self):
        try:
            self.ruler.queue_draw()
        except AttributeError:
            pass

    def follow_playhead(self):
        """Keep a running playhead on screen.

        BY THE PAGE, NOT BY THE PIXEL — and then the page TRAVELS. A view that
        slid continuously under the playhead would rebuild every lane's cached
        surface ten times a second, which is the one thing the whole cache
        exists to avoid on a machine with no GPU; and a picture that never
        stops moving is harder to read than one that turns a page. So the view
        moves only when the playhead has actually left it, and when it does it
        animates across like any other state change rather than cutting.

        It must not act while a view animation is ALREADY in flight: this runs
        on the 100ms transport tick, and the page it is travelling towards does
        not contain the playhead until it arrives, so every tick in between
        would retarget the animation to a fractionally later page and the view
        would creep instead of turning."""
        if self.transport not in ("play", "rec") or self.zoom <= ZOOM_FIT:
            return
        if self.view_moving():
            return
        span = self.view_span()
        lo = self.view_start
        if lo <= self.pos <= lo + span:
            return
        self._animate_view(self.zoom, self.pos - span * FOLLOW_AT)

    def wheel_over_timeline(self, ev, W, t_at, plain_scrolls=False,
                            x_offset=0.0):
        """Ctrl+wheel zooms about the pointer, Shift+wheel scrolls sideways.

        A plain wheel over a LANE is left alone, because it still has to scroll
        the stack of eight tracks — that is what a wheel does everywhere else
        in the OS. Over the RULER, which scrolls nothing, a plain wheel scrolls
        the tape instead of doing nothing at all."""
        d = 0
        if ev.direction == Gdk.ScrollDirection.UP:
            d = -1
        elif ev.direction == Gdk.ScrollDirection.DOWN:
            d = 1
        elif ev.direction == Gdk.ScrollDirection.SMOOTH:
            ok, _dx, dy = ev.get_scroll_deltas()
            if not ok or abs(dy) < 0.01:
                return False
            d = 1 if dy > 0 else -1
        else:
            return False
        state = ev.state
        if state & Gdk.ModifierType.CONTROL_MASK:
            at = t_at(ev.x)
            frac = max(0.0, min(1.0, (ev.x - x_offset) / max(1, W)))
            self.zoom_by(1.0 / ZOOM_STEP if d > 0 else ZOOM_STEP, at, frac)
            return True
        if (state & Gdk.ModifierType.SHIFT_MASK) or plain_scrolls:
            self.scroll_view(d * self.view_span() * 0.22)
            return True
        return False

    # ================= the editor view =================
    def _editor_view(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.pack_start(self._editor_bar(), False, False, 0)

        self.wave_edit = WaveEdit(self)

        # Two pages: the take, or a note saying why there isn't one. A stack
        # rather than one widget that draws both, so the empty state is a real
        # centred message and not a caption in the corner of a blank canvas.
        self.edit_stack = Gtk.Stack()
        self.edit_stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.edit_stack.add_named(self.wave_edit, "wave")
        self.edit_stack.add_named(self._editor_empty(), "empty")
        box.pack_start(self.edit_stack, True, True, 0)
        return box

    def _editor_empty(self):
        """What EDIT shows when there is no take to show."""
        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        b.set_valign(Gtk.Align.CENTER)
        b.set_halign(Gtk.Align.CENTER)
        self.edit_empty_lbl = Gtk.Label(label="")
        self.edit_empty_lbl.get_style_context().add_class("emptybig")
        self.edit_empty_lbl.set_justify(Gtk.Justification.CENTER)
        b.pack_start(self.edit_empty_lbl, False, False, 0)
        return b

    def _editor_bar(self):
        """What one take can be told to do: how loud, how it starts and ends,
        where it sits against the bars, and going round and round while it is
        played to."""
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        bar.get_style_context().add_class("editbar")
        bar.set_valign(Gtk.Align.CENTER)

        self.clip_lbl = Gtk.Label(label="")
        self.clip_lbl.get_style_context().add_class("cliplbl")
        self.clip_lbl.set_xalign(0)
        self.clip_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self.clip_lbl.set_max_width_chars(30)
        bar.pack_start(self.clip_lbl, False, False, 0)
        bar.pack_start(self._vsep(), False, False, 0)

        # ---- a recorded take ----
        self.take_tools = Gtk.Box(spacing=10)
        self.take_tools.pack_start(self._caplabel("Level"), False, False, 0)
        self.cgain = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,
                                              0, CLIP_GAIN_MAX, 1)
        self.cgain.set_draw_value(False)
        self.cgain.add_mark(GAIN_UNITY, Gtk.PositionType.BOTTOM, None)
        self.cgain.set_value(GAIN_UNITY)
        self.cgain.set_size_request(96, -1)
        self.cgain.set_tooltip_text(_t("Level of this take on its own"))
        self.cgain.connect("value-changed", self._on_clip_gain)
        self.take_tools.pack_start(self.cgain, False, False, 0)
        self.cgain_lbl = Gtk.Label(label="")
        self.cgain_lbl.get_style_context().add_class("smallnum")
        self.cgain_lbl.set_size_request(52, -1)
        self.cgain_lbl.set_xalign(0)
        self.take_tools.pack_start(self.cgain_lbl, False, False, 0)

        self.take_tools.pack_start(self._caplabel("Fade in"), False, False, 0)
        self.fin_btn = self._chip("5 MS", lambda: self._cycle_fade("fin"),
                                  _t("Length of the fade at the start"))
        self.take_tools.pack_start(self.fin_btn, False, False, 0)
        self.take_tools.pack_start(self._caplabel("Fade out"), False, False, 0)
        self.fout_btn = self._chip("5 MS", lambda: self._cycle_fade("fout"),
                                   _t("Length of the fade at the end"))
        self.take_tools.pack_start(self.fout_btn, False, False, 0)

        norm = Gtk.Button(label=_t("Normalise"))
        norm.set_relief(Gtk.ReliefStyle.NONE)
        norm.get_style_context().add_class("editact")
        norm.set_tooltip_text(
            _t("Set this take's level so its loudest moment is just below "
               "full"))
        norm.connect("clicked", lambda *_: self._normalise_clip())
        self.take_tools.pack_start(norm, False, False, 0)
        snap = Gtk.Button(label=_t("Snap to Grid"))
        snap.set_relief(Gtk.ReliefStyle.NONE)
        snap.get_style_context().add_class("editact")
        snap.set_tooltip_text(
            _t("Move this take so it starts on the nearest grid line"))
        snap.connect("clicked", lambda *_: self._snap_clip())
        self.take_tools.pack_start(snap, False, False, 0)
        loop = Gtk.Button(label=_t("Loop This"))
        loop.set_relief(Gtk.ReliefStyle.NONE)
        loop.get_style_context().add_class("editact")
        loop.set_tooltip_text(_t("Play these bars over and over"))
        loop.connect("clicked", lambda *_: self._loop_selected())
        self.take_tools.pack_start(loop, False, False, 0)
        bar.pack_start(self.take_tools, False, False, 0)

        bar.pack_start(Gtk.Box(), True, True, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        try:
            scroller.set_propagate_natural_height(True)
        except AttributeError:
            pass
        scroller.get_style_context().add_class("transportscroll")
        scroller.add(bar)
        return scroller

    def _chip(self, label, cb, tip=None):
        b = Gtk.Button(label=label)
        b.set_relief(Gtk.ReliefStyle.NONE)
        b.get_style_context().add_class("chip")
        if tip:
            b.set_tooltip_text(tip)
        b.connect("clicked", lambda *_: cb())
        return b

    def _vsep(self):
        s = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        s.get_style_context().add_class("vsep")
        return s

    # ---- editor controls ---------------------------------------------------
    def _on_clip_gain(self, scale):
        c = self.sel_clip()
        if self._loading or c is None:
            return
        c["gain"] = scale.get_value() / 100.0
        self._save_soon()
        self._engine_changed()
        self._sync_editor()
        self.wave_edit.queue_draw()

    def _cycle_fade(self, which):
        """Step a clip's fade through the usual lengths.

        A few milliseconds is a de-click — the difference between a take that
        starts and one that starts with a tick — and everything above that is a
        musical fade. Cycling beats a slider here: there are only ever a handful
        of useful answers."""
        c = self.sel_clip()
        if c is None:
            return
        cur = c.get(which, 0.005)
        nxt = next((v for v in FADE_CHOICES if v > cur + 1e-9),
                   FADE_CHOICES[0])
        span = c["e"] - c["s"]
        # a fade longer than the clip is a fade the clip cannot show or play
        if nxt > span * 0.5:
            nxt = FADE_CHOICES[0]
        self._remember("Fade")
        c[which] = nxt
        self.clip_changed()
        self.wave_edit.queue_draw()

    def _snap_clip(self):
        """Move the selected clip so it starts on the nearest grid line.

        A take starts when Record was pressed, which is never exactly on a bar.
        Nothing else about the take changes — the same audio plays, a fraction
        of a second earlier or later — and this is what lines a recorded loop
        up with everything else. With SNAP set to FREE there is no grid to
        line it up to, so this falls back to the bar, which is the answer
        somebody pressing a button called "Snap" is asking for."""
        c = self.sel_clip()
        if c is None:
            return
        step = self.snap_seconds() or self.sec_per_bar()
        s = round(c["s"] / step) * step if step > 0 else c["s"]
        s = max(0.0, min(self.length - (c["e"] - c["s"]), s))
        if abs(s - c["s"]) < 1e-6:
            return
        self._remember("Snap to Grid")
        span = c["e"] - c["s"]
        c["s"], c["e"] = s, s + span
        self.clip_changed()

    def _normalise_clip(self):
        """Set the selected take's own level so its loudest moment sits just
        below full scale.

        A microphone gain set by guessing is the normal way a take arrives, and
        every take arrives at a different one. This is the fastest honest fix:
        nothing is written back to the recording, only the number the clip is
        multiplied by on its way into the mix, so it is exact, instant and
        undoable."""
        c = self.sel_clip()
        if c is None or not c.get("wav"):
            return
        pk = clip_peak(c)
        if pk < 0:
            self._flash(_t("This take's recording can't be read"))
            return
        if pk <= 0.0:
            self._flash(_t("This take is silent, so there is nothing to set"))
            return
        # -0.5 dBFS: right at the top invites a stray sample over it once the
        # track's tone and the master fader have had their say. A take quieter
        # than the ceiling can lift is brought up as far as it goes and SAID
        # so, rather than silently landing somewhere short of the mark.
        target = min(CLIP_GAIN_MAX / 100.0, 0.944 / pk)
        if abs(target - c.get("gain", 1.0)) < 0.005:
            self._flash(_t("This take is already at its level"))
            return
        self._remember("Normalise")
        c["gain"] = target
        self._sync_editor()
        self.clip_changed()
        self.wave_edit.queue_draw()
        db = int(round(20 * math.log10(max(0.001, target))))
        if target * pk < 0.9:
            self._flash(_t("Take level set to %+d dB — as far as it goes; "
                           "this take was recorded very quietly") % db)
        else:
            self._flash(_t("Take level set to %+d dB") % db)

    def _show_editor(self, name):
        """Switch the editor to the take or to the empty note.

        Through the same show-it-first dance _set_view needs: a GtkStack will
        not switch to a child that has never been shown, so an editor chosen
        before the window is on screen would leave the two disagreeing."""
        try:
            child = self.edit_stack.get_child_by_name(name)
            if child is not None:
                child.show_all()
            self.edit_stack.set_visible_child_name(name)
        except Exception:
            pass

    def _sync_editor(self):
        """Push the selected take's state into the editor's controls."""
        if not hasattr(self, "take_tools"):
            return
        c = self.sel_clip()
        tk = self.sel_track()
        self.take_tools.set_visible(bool(c) and bool(c["wav"]))
        self.take_tools.set_no_show_all(not self.take_tools.get_visible())
        if c is None or tk is None:
            self.clip_lbl.set_text(_t("Nothing selected"))
            self.edit_empty_lbl.set_text(
                _t("Arm a track, press Record, and the take lands here.\n"
                   "Click one on a lane to open it."))
            self._show_editor("empty")
            return
        bar1, beat1 = self.bar_beat_at(c["s"])
        self.clip_lbl.set_text(
            "%s · %s" % (tk["name"],
                         _t("bar %d.%d, %s long")
                         % (bar1, beat1, _fmt_dur(c["e"] - c["s"]))))
        if not c["wav"]:
            self.edit_empty_lbl.set_text(
                _t("This block has no recording in it yet.\n"
                   "Arm the track and record over it."))
            self._show_editor("empty")
        else:
            self._loading = True
            try:
                self.cgain.set_value(round(c.get("gain", 1.0) * 100))
            finally:
                self._loading = False
            g = c.get("gain", 1.0)
            self.cgain_lbl.set_text(
                "−∞" if g <= 0.001
                else ("0 dB" if abs(g - 1.0) < 0.005
                      else "%+d dB" % int(round(20 * math.log10(g)))))
            self.fin_btn.set_label(_fmt_fade(c.get("fin", 0.0)))
            self.fout_btn.set_label(_fmt_fade(c.get("fout", 0.0)))
            self._show_editor("wave")
            self.wave_edit.queue_draw()

    # ================= the mixer view =================
    def _mixer_view(self):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        row.get_style_context().add_class("mixer")
        self.strips = []
        for i in range(len(self.tracks)):
            row.pack_start(self._strip(i), False, False, 0)
        row.pack_start(self._master_strip(), False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.add(row)
        return scroll

    def _strip(self, i):
        tk = self.tracks[i]
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        col.get_style_context().add_class("strip")
        # Eight of these plus the master have to fit the narrowest panel we
        # support (1024) without the master — the one strip that governs the
        # whole mix — being the one that scrolls off the edge.
        col.set_size_request(104, -1)

        name = Gtk.Label(label=tk["name"])
        name.get_style_context().add_class("stripname")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(11)
        name.set_xalign(0)
        col.pack_start(name, False, False, 0)

        pan = self._mini_scale(-PAN_MAX, PAN_MAX, tk["pan"], 0,
                               lambda s, k=i: self._on_pan(s, k),
                               _t("Left / right placement"))
        col.pack_start(self._labelled("PAN", pan), False, False, 0)
        # A microphone in a bedroom picks up more below the music than in it,
        # and eight tracks of that is most of what makes a home recording sound
        # like one. These two are the tone control, and they are on every strip
        # because the first one is the most useful thing here.
        low = self._mini_scale(0, 100, tk["low"], None,
                               lambda s, k=i: self._on_send(s, k, "low"),
                               _t("Takes the rumble out of this track"))
        col.pack_start(self._labelled("LO CUT", low), False, False, 0)
        high = self._mini_scale(0, 100, tk["high"], None,
                                lambda s, k=i: self._on_send(s, k, "high"),
                                _t("Takes the top off this track"))
        col.pack_start(self._labelled("HI CUT", high), False, False, 0)
        comp = self._mini_scale(0, 100, tk["comp"], None,
                                lambda s, k=i: self._on_send(s, k, "comp"),
                                _t("Evens out the loud and quiet parts"))
        col.pack_start(self._labelled("EVEN", comp), False, False, 0)
        rev = self._mini_scale(0, SEND_MAX, tk["rev"], None,
                               lambda s, k=i: self._on_send(s, k, "rev"),
                               _t("How much of this track goes to the room"))
        col.pack_start(self._labelled("ROOM", rev), False, False, 0)
        dly = self._mini_scale(0, SEND_MAX, tk["dly"], None,
                               lambda s, k=i: self._on_send(s, k, "dly"),
                               _t("How much of this track goes to the echo"))
        col.pack_start(self._labelled("ECHO", dly), False, False, 0)

        fader = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        fader.set_halign(Gtk.Align.CENTER)
        gain = Gtk.Scale.new_with_range(Gtk.Orientation.VERTICAL,
                                        0, GAIN_MAX, 1)
        gain.set_inverted(True)          # loud at the top, like every desk
        gain.set_draw_value(False)
        gain.add_mark(GAIN_UNITY, Gtk.PositionType.LEFT, None)
        gain.set_value(tk["gain"])
        gain.set_size_request(-1, 110)
        gain.set_tooltip_text(_t("Level"))
        gain.connect("value-changed", self._on_gain, i)
        fader.pack_start(gain, False, False, 0)
        vu = VU(9, seg_w=9, seg_h=6, gap=2, bg=RAIL)
        vu.set_vertical(True)
        fader.pack_start(vu, False, False, 0)
        col.pack_start(fader, True, True, 0)

        glbl = Gtk.Label(label="")
        glbl.get_style_context().add_class("smallnum")
        col.pack_start(glbl, False, False, 0)

        btns = Gtk.Box(spacing=4)
        btns.set_halign(Gtk.Align.CENTER)
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
        btns.pack_start(mute, False, False, 0)
        btns.pack_start(solo, False, False, 0)
        col.pack_start(btns, False, False, 0)

        self.strips.append({"name": name, "pan": pan,
                            "low": low, "high": high, "comp": comp,
                            "rev": rev, "dly": dly, "gain": gain, "vu": vu,
                            "gainlbl": glbl, "mute": mute, "solo": solo})
        return col

    def _labelled(self, cap, widget):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        lbl = Gtk.Label(label=_t(cap))
        lbl.get_style_context().add_class("minicap")
        lbl.set_xalign(0)
        lbl.set_size_request(42, -1)
        box.pack_start(lbl, False, False, 0)
        box.pack_start(widget, True, True, 0)
        return box

    def _mini_scale(self, lo, hi, value, mark, cb, tip):
        s = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, 1)
        s.set_draw_value(False)
        if mark is not None:
            s.add_mark(mark, Gtk.PositionType.BOTTOM, None)
        s.set_value(value)
        s.set_size_request(42, -1)
        s.set_tooltip_text(tip)
        s.connect("value-changed", cb)
        return s

    def _master_strip(self):
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        col.get_style_context().add_class("strip")
        col.get_style_context().add_class("masterstrip")
        col.set_size_request(150, -1)

        head = Gtk.Label(label=_t("MASTER"))
        head.get_style_context().add_class("stripname")
        head.set_xalign(0)
        col.pack_start(head, False, False, 0)

        self.fx_btn = Gtk.Button(label=_t("EFFECTS"))
        self.fx_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.fx_btn.get_style_context().add_class("metrobtn")
        self.fx_btn.set_tooltip_text(
            _t("Room, echo and tape for the whole mix"))
        self.fx_btn.connect("clicked", lambda *_: self._toggle_fx())
        col.pack_start(self.fx_btn, False, False, 0)

        self.rev_size_s = self._mini_scale(
            0, 100, self.rev_size, None,
            lambda s: self._on_master_fx(s, "rev_size"),
            _t("Size of the room"))
        col.pack_start(self._labelled("ROOM", self.rev_size_s), False, False, 0)
        self.rev_mix_s = self._mini_scale(
            0, 100, self.rev_mix, None,
            lambda s: self._on_master_fx(s, "rev_mix"),
            _t("How much room is heard"))
        col.pack_start(self._labelled("WET", self.rev_mix_s), False, False, 0)

        self.dly_time_btn = self._chip(
            "1/8", self._cycle_delay_time, _t("Time between echoes"))
        col.pack_start(self._labelled("ECHO", self.dly_time_btn),
                       False, False, 0)
        self.dly_fb_s = self._mini_scale(
            0, 90, self.dly_fb, None,
            lambda s: self._on_master_fx(s, "dly_fb"),
            _t("How many times the echo repeats"))
        col.pack_start(self._labelled("REPEAT", self.dly_fb_s),
                       False, False, 0)

        self.tape_s = self._mini_scale(
            0, 100, self.tape, None,
            lambda s: self._on_master_fx(s, "tape"),
            _t("Tape wobble and saturation over the whole mix"))
        col.pack_start(self._labelled("TAPE", self.tape_s), False, False, 0)

        fader = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        fader.set_halign(Gtk.Align.CENTER)
        self.master_fader = Gtk.Scale.new_with_range(
            Gtk.Orientation.VERTICAL, 0, GAIN_MAX, 1)
        self.master_fader.set_inverted(True)
        self.master_fader.set_draw_value(False)
        self.master_fader.add_mark(GAIN_UNITY, Gtk.PositionType.LEFT, None)
        self.master_fader.set_value(self.master)
        self.master_fader.set_size_request(-1, 110)
        self.master_fader.set_tooltip_text(_t("Level of the whole mix"))
        self.master_fader.connect("value-changed", self._on_master)
        fader.pack_start(self.master_fader, False, False, 0)
        self.master_vu2 = VU(9, seg_w=9, seg_h=6, gap=2, bg=RAIL)
        self.master_vu2.set_vertical(True)
        fader.pack_start(self.master_vu2, False, False, 0)
        self.master_vu3 = VU(9, seg_w=9, seg_h=6, gap=2, bg=RAIL)
        self.master_vu3.set_vertical(True)
        fader.pack_start(self.master_vu3, False, False, 0)
        col.pack_start(fader, True, True, 0)

        self.master_lbl2 = Gtk.Label(label="")
        self.master_lbl2.get_style_context().add_class("smallnum")
        col.pack_start(self.master_lbl2, False, False, 0)
        return col

    # ---- mixer handlers ----------------------------------------------------
    def _on_pan(self, scale, i):
        if self._loading:
            return
        self.tracks[i]["pan"] = int(scale.get_value())
        self._save_soon()
        self._engine_changed()
        self.refresh()

    def _on_send(self, scale, i, key):
        if self._loading:
            return
        self.tracks[i][key] = int(scale.get_value())
        self._save_soon()
        self._engine_changed()
        self.refresh()

    def _on_master_fx(self, scale, key):
        if self._loading:
            return
        setattr(self, key, int(scale.get_value()))
        self._save_soon()
        self._engine_changed()
        self.refresh()

    def _toggle_fx(self):
        self.fx = not self.fx
        self._save_soon()
        self._engine_changed()
        self.refresh()

    DELAY_TIMES = ((0.25, "1/16"), (0.5, "1/8"), (0.75, "1/8 dotted"),
                   (1.0, "1/4"), (1.5, "1/4 dotted"), (2.0, "1/2"))

    def _cycle_delay_time(self):
        vals = [v for (v, _n) in self.DELAY_TIMES]
        try:
            i = vals.index(self.dly_time)
        except ValueError:
            i = 1
        self.dly_time = vals[(i + 1) % len(vals)]
        self._save_soon()
        self._engine_changed()
        self.refresh()

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
        # head's gain row needs 283 and GTK honours the larger — so put them
        # in one size group and the cell adopts the head's REAL width in the
        # first allocation, whatever the font or a translated label does to it.
        self._head_group = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        self._head_group.add_widget(rcell)
        self.ruler = Ruler(self)
        rrow.pack_start(self.ruler, True, True, 0)
        # ...and a gutter that mirrors the lane scrollbar, so bar 33 on the
        # ruler stays exactly above bar 33 in the lanes once the stack scrolls
        # (without it every lane is a scrollbar narrower than the ruler and the
        # playhead drifts off the grid towards the end of the tape).
        self._ruler_gutter = Gtk.Box()
        rrow.pack_start(self._ruler_gutter, False, False, 0)
        area.pack_start(self._timeline_bar(), False, False, 0)
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

        # The tape's own scrollbar, under the lanes and lined up with them.
        # It is a real scrollbar and not a pair of arrows because a scrollbar
        # is also a MAP: its thumb says how much of the song is on screen and
        # whereabouts in it, which is the one thing zooming in takes away.
        hrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        hrow.get_style_context().add_class("hscrollrow")
        hcell = Gtk.Box()
        hcell.set_size_request(262, -1)
        self._head_group.add_widget(hcell)
        hrow.pack_start(hcell, False, False, 0)
        self._hadj = Gtk.Adjustment(value=0.0, lower=0.0, upper=self.length,
                                    step_increment=1.0,
                                    page_increment=self.length,
                                    page_size=self.length)
        self._hadj.connect("value-changed", self._on_hscroll)
        self._hscroll = Gtk.Scrollbar(orientation=Gtk.Orientation.HORIZONTAL,
                                      adjustment=self._hadj)
        hrow.pack_start(self._hscroll, True, True, 0)
        self._hscroll_gutter = Gtk.Box()
        hrow.pack_start(self._hscroll_gutter, False, False, 0)
        area.pack_start(hrow, False, False, 0)

        vsb = scroll.get_vscrollbar()
        if vsb is not None:
            vsb.connect("size-allocate", self._sync_ruler_gutter)
            vsb.connect("notify::visible", self._sync_ruler_gutter)
            # the tape scrollbar needs the same gutter for the same reason:
            # its thumb has to end where the lanes end, or the map is a
            # scrollbar's width out from the thing it maps
            vsb.connect("size-allocate", self._sync_hscroll_gutter)
            vsb.connect("notify::visible", self._sync_hscroll_gutter)
        self._sync_hscroll()
        return area

    # ---- the timeline's own controls --------------------------------------
    def _timeline_bar(self):
        """Tool, grid and zoom: the three things that decide what a gesture on
        a lane MEANS, in the view they act on and nowhere else.

        Not on the transport deck, which already scrolls sideways on a narrow
        panel — a control that decides whether a click cuts a recording in half
        must not be one that can be off the edge of the screen."""
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        bar.get_style_context().add_class("timelinebar")

        seg = Gtk.Box(spacing=0)
        seg.get_style_context().add_class("seg")
        self.tool_btns = {}
        for key, label, tip in (
                (TOOL_SELECT, "SELECT",
                 _t("Click to select, drag to move, drag an end to trim")),
                (TOOL_CUT, "CUT", _t("Click a clip to cut it in two"))):
            b = Gtk.Button(label=_t(label))
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("segbtn")
            b.set_tooltip_text(tip)
            b.connect("clicked", lambda _b, k=key: self._set_tool(k))
            self.tool_btns[key] = b
            seg.pack_start(b, False, False, 0)
        bar.pack_start(seg, False, False, 0)

        bar.pack_start(self._vsep(), False, False, 0)
        bar.pack_start(self._caplabel("Snap"), False, False, 0)
        self.snap_btn = self._chip(
            "BAR", self._cycle_snap,
            _t("What edits land on: a bar, a beat, a division of one — or "
               "FREE, which is exactly where the pointer is"))
        bar.pack_start(self.snap_btn, False, False, 0)

        bar.pack_start(self._vsep(), False, False, 0)
        bar.pack_start(self._caplabel("Zoom"), False, False, 0)
        zout = Gtk.Button(label="−")
        zout.set_relief(Gtk.ReliefStyle.NONE)
        zout.get_style_context().add_class("zbtn")
        zout.set_tooltip_text(_t("Zoom out    −"))
        zout.connect("clicked", lambda *_: self._zoom_step(1.0 / ZOOM_STEP))
        bar.pack_start(zout, False, False, 0)
        zin = Gtk.Button(label="+")
        zin.set_relief(Gtk.ReliefStyle.NONE)
        zin.get_style_context().add_class("zbtn")
        zin.set_tooltip_text(_t("Zoom in    +"))
        zin.connect("clicked", lambda *_: self._zoom_step(ZOOM_STEP))
        bar.pack_start(zin, False, False, 0)
        self.zoom_lbl = Gtk.Label(label="")
        self.zoom_lbl.get_style_context().add_class("smallnum")
        self.zoom_lbl.set_size_request(74, -1)
        self.zoom_lbl.set_xalign(0)
        self.zoom_lbl.set_tooltip_text(_t("How much of the song is on screen"))
        bar.pack_start(self.zoom_lbl, False, False, 0)
        fit = Gtk.Button(label=_t("FIT"))
        fit.set_relief(Gtk.ReliefStyle.NONE)
        fit.get_style_context().add_class("chip")
        fit.set_tooltip_text(_t("Show the whole song    Ctrl+0"))
        fit.connect("clicked", lambda *_: self.zoom_fit())
        bar.pack_start(fit, False, False, 0)
        loopz = Gtk.Button(label=_t("TO LOOP"))
        loopz.set_relief(Gtk.ReliefStyle.NONE)
        loopz.get_style_context().add_class("chip")
        loopz.set_tooltip_text(_t("Fill the screen with the looped bars"))
        loopz.connect("clicked", lambda *_: self._zoom_to_loop())
        bar.pack_start(loopz, False, False, 0)

        bar.pack_start(Gtk.Box(), True, True, 0)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        try:
            scroller.set_propagate_natural_height(True)
        except AttributeError:
            pass
        scroller.get_style_context().add_class("transportscroll")
        scroller.add(bar)
        return scroller

    def _set_tool(self, key):
        self.tool = key if key in (TOOL_SELECT, TOOL_CUT) else TOOL_SELECT
        for k, b in self.tool_btns.items():
            _cls(b, "on", k == self.tool)
        for lane in getattr(self, "lanes", []):
            lane.queue_draw()

    def _cycle_snap(self, *_):
        vals = [v for (v, _n) in SNAP_CHOICES]
        try:
            i = vals.index(self.snap)
        except ValueError:
            i = 0
        self.snap = vals[(i + 1) % len(vals)]
        self._update_snap_btn()
        self._save_soon()
        for lane in getattr(self, "lanes", []):
            lane.queue_draw()

    def _update_snap_btn(self):
        name = next((n for (v, n) in SNAP_CHOICES if v == self.snap), "BAR")
        self.snap_btn.set_label(_t(name))
        _cls(self.snap_btn, "on", self.snap != SNAP_FREE)

    def _zoom_step(self, factor):
        """Zoom about the PLAYHEAD when it is on screen, otherwise about the
        middle: the playhead is where the work is, and a button press that
        scrolled away from it would have to be undone by hand every time."""
        span = self.view_span()
        anchor, frac = self.pos, FOLLOW_AT
        if not (self.view_start <= self.pos <= self.view_start + span):
            anchor, frac = self.view_start + span * 0.5, 0.5
        self.zoom_by(factor, anchor, frac)

    def _zoom_to_loop(self, *_):
        if self.loop_e - self.loop_s > 0.05:
            self.zoom_to(self.loop_s, self.loop_e)
        else:
            self._flash(_t("Drag across the ruler to choose the bars to loop"))

    def _update_zoom_btn(self):
        if not hasattr(self, "zoom_lbl"):
            return
        # What is on screen, in seconds — the honest reading of a zoom. "x12"
        # means nothing without knowing how long the song is.
        self.zoom_lbl.set_text(_fmt_dur(self.view_span()))

    def _sync_hscroll(self):
        """Point the scrollbar at the window the lanes are showing."""
        if not hasattr(self, "_hadj") or self._hs_sync:
            return
        span = self.view_span()
        self._hs_sync = True
        try:
            self._hadj.set_upper(self.length)
            self._hadj.set_page_size(span)
            self._hadj.set_page_increment(span * 0.9)
            self._hadj.set_step_increment(max(0.05, span * 0.1))
            self._hadj.set_value(self.view_start)
        finally:
            self._hs_sync = False
        # Remember what we put there, because the flag above is NOT enough:
        # changing page_size re-clamps the value and GTK3 emits the resulting
        # value-changed LATER, by which time the flag is clear again. See
        # _on_hscroll.
        self._hs_last = self._hadj.get_value()
        # A bar that can never move is noise; it goes away at FIT and comes
        # back the moment there is something off screen to reach.
        self._hscroll.set_sensitive(span < self.length - 1e-6)

    def _on_hscroll(self, adj):
        """The scrollbar moves the view DIRECTLY, with no animation.

        The one place a view change must not be eased: the thumb is under the
        user's finger and already is the animation. Springing to catch up with
        it would put the tape behind the hand dragging it, and the overshoot
        would fight the next motion event."""
        if self._hs_sync:
            return
        # READ THE THUMB FIRST. Cancelling a live animation lands it on its own
        # target, and landing calls _after_view_change -> _sync_hscroll, which
        # writes that target back into THIS adjustment — so asking `adj` for its
        # value afterwards returns where the animation was going instead of
        # where the hand just put it.
        want = adj.get_value()
        # ...AND IGNORE OUR OWN WRITE COMING BACK. The _hs_sync flag only
        # catches a SYNCHRONOUS echo, and GTK3's does not have to be: setting
        # page_size re-clamps the value and the value-changed for it arrives
        # after the flag is clear. So a programmatic sync bounced back into
        # here a moment later and re-asserted the adjustment over the app —
        # harmless while the two agreed, and a snap-back to the abandoned
        # target the one time they did not: a drag during a zoom. Comparing
        # VALUES instead of trusting a flag is immune to when the signal lands.
        if self._hs_last is not None and abs(want - self._hs_last) < 1e-9:
            return
        if self.view_moving():
            # a drag wins over anything in flight: the hand is the authority
            nbmotion.cancel_all(self.stack)
            self._view_moving = False
            self._view_target_start = self._view_target_zoom = None
        self.view_start = want
        self._clamp_view()
        self.sync_ruler()
        for lane in getattr(self, "lanes", []):
            lane.queue_draw()

    def _sync_hscroll_gutter(self, bar, *_a):
        w = bar.get_allocated_width() if bar.get_visible() else 0
        if w != self._hscroll_gutter.get_size_request()[0]:
            self._hscroll_gutter.set_size_request(w, -1)

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
        # The lane's NAME, typed. A track is whatever the person making the
        # record says it is — "Verse keys", "Bass DI" — and that used to have to
        # be typed into the instrument slot for want of anywhere else. The slot
        # below now names a real sound, so the label came here.
        name = Gtk.Entry()
        name.set_text(self.tracks[i]["name"])
        name.get_style_context().add_class("nameentry")
        name.set_width_chars(9)
        name.set_max_length(48)
        name.set_tooltip_text(_t("Name of this track"))
        name.connect("changed", self._on_track_name, i)
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
            nbicons.set_image(clrimg, "trash", 15, "#6E695E")
        except GLib.Error:
            pass
        clr.add(clrimg)
        clr.set_tooltip_text(_t("Remove this track's clips"))
        clr.connect("clicked", self._clear_track, i)
        mbtns.pack_start(mute, False, False, 0)
        mbtns.pack_start(solo, False, False, 0)
        mbtns.pack_start(arm, False, False, 0)
        mbtns.pack_start(clr, False, False, 0)
        r1.pack_start(mbtns, False, False, 0)
        head.pack_start(r1, False, False, 0)

        # row 2: the meter. It used to share this row with a control naming
        # the sound the track played, back when a track could be a drum
        # machine; every track is a recording now, so the whole row is the
        # meter — and a meter twice as long is a meter you can set a level by
        # rather than one that says "loud" or "not loud".
        r2 = Gtk.Box(spacing=6)
        r2.pack_start(self._minicap("Lvl"), False, False, 0)
        vu = VU(18, seg_w=9, seg_h=10)
        r2.pack_start(vu, False, False, 0)
        r2.pack_start(Gtk.Box(), True, True, 0)
        head.pack_start(r2, False, False, 0)

        # row 3: gain. Pan lives on the mixer strip, where the rest of the
        # placement controls are; a second copy of it on a 262px head was a
        # fader nobody could aim.
        r3 = Gtk.Box(spacing=6)
        gcap = self._minicap("G")
        gcap.set_size_request(10, -1)
        r3.pack_start(gcap, False, False, 0)
        gain = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,
                                        0, GAIN_MAX, 1)
        gain.set_draw_value(False)
        gain.add_mark(GAIN_UNITY, Gtk.PositionType.BOTTOM, None)
        gain.set_value(self.tracks[i]["gain"])
        gain.set_hexpand(True)
        gain.set_size_request(60, -1)
        gain.set_tooltip_text(_t("Gain"))
        gain.connect("value-changed", self._on_gain, i)
        r3.pack_start(gain, True, True, 0)
        gainlbl = Gtk.Label()
        gainlbl.get_style_context().add_class("smallnum")
        gainlbl.set_size_request(46, -1)
        gainlbl.set_xalign(1)
        r3.pack_start(gainlbl, False, False, 0)
        head.pack_start(r3, False, False, 0)

        row.pack_start(head, False, False, 0)

        lane = Lane(self, i)
        self.lanes.append(lane)
        row.pack_start(lane, True, True, 0)

        self.track_widgets.append({
            "head": head, "name": name, "mute": mute, "solo": solo,
            "arm": arm, "vu": vu, "gain": gain,
            "gainlbl": gainlbl, "clr": clr})
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
        self.proj_lbl = Gtk.Label(label=_t("Not saved to a file"))
        self.proj_lbl.set_xalign(1)
        self.proj_lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        bar.pack_end(self.proj_lbl, True, True, 0)
        return bar

    # ================= interaction =================
    def _busy_overlay(self):
        """True when a confirm card, the About card or an open menu owns the
        keyboard. Nothing here may act from behind one of those: the key has to
        fall through and activate the focused Cancel / OK / menu item."""
        return (self._prompt_layer is not None or self._menu_open is not None
                or getattr(self, "_about_layer", None) is not None)

    def _on_space(self, _w, ev):
        """The single-key editing shortcuts.

        Suppressed while a track name is being TYPED, or 'c' in the middle of
        "Chorus 2" would arm the knife instead of appearing in the box. A
        focused text field gets EVERY plain key to itself — including Space,
        or the space in "Chorus 2" would toggle playback instead of landing
        in the name (the transport's Space works from anywhere else). Keys
        with a modifier keep working."""
        if self._busy_overlay():
            return False
        kv = ev.keyval
        mods = ev.state & (Gdk.ModifierType.CONTROL_MASK
                           | Gdk.ModifierType.MOD1_MASK)
        shift = bool(ev.state & Gdk.ModifierType.SHIFT_MASK)
        typing = isinstance(self.get_focus(), (Gtk.Editable, Gtk.TextView))
        if typing or mods:
            return False
        if kv == Gdk.KEY_space:
            self._toggle_play()
            return True
        if kv in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
            self._zoom_step(ZOOM_STEP)
            return True
        if kv in (Gdk.KEY_minus, Gdk.KEY_underscore, Gdk.KEY_KP_Subtract):
            self._zoom_step(1.0 / ZOOM_STEP)
            return True
        if kv in (Gdk.KEY_c, Gdk.KEY_C):
            self._toggle_tool()
            return True
        if kv in (Gdk.KEY_s, Gdk.KEY_S):
            self._split_at_playhead()
            return True
        if kv in (Gdk.KEY_Delete, Gdk.KEY_BackSpace, Gdk.KEY_KP_Delete):
            self._delete_selected()
            return True
        if kv in (Gdk.KEY_Left, Gdk.KEY_KP_Left):
            self._nudge_selected(-1, shift)
            return True
        if kv in (Gdk.KEY_Right, Gdk.KEY_KP_Right):
            self._nudge_selected(1, shift)
            return True
        if kv in (Gdk.KEY_Up, Gdk.KEY_KP_Up):
            self._nudge_track(-1)
            return True
        if kv in (Gdk.KEY_Down, Gdk.KEY_KP_Down):
            self._nudge_track(1)
            return True
        return False

    def _start_audio(self, at=None):
        """Start rendering and playing the arrangement from `at` seconds.

        Called on every Play and Record rather than once per session: the
        renderer has to be handed the song as it stands and the position to
        start from, and a stopped transport holds no sound device at all."""
        if at is None:
            at = self.pos
        self._free_pos = at
        self._free_t0 = time.monotonic()
        song = self._song()
        if self.transport == "rec":
            # A loop while recording would punch the same bars over and over
            # and leave one take on top of another with no way to tell them
            # apart. Recording runs straight through.
            song["loop"] = None
        self.engine.start(song, at, metronome=self.metronome)
        self._update_audio_lbl()

    def _engine_changed(self):
        """Tell a running renderer the arrangement moved under it.

        Every edit goes through here, so a hit programmed, a fader moved or a
        track muted during playback is heard about a second later — the depth
        of the lookahead — instead of at the next Play."""
        if self.transport in ("play", "rec") and self.engine.available:
            song = self._song()
            if self.transport == "rec":
                song["loop"] = None
            self.engine.update(song)

    def _ensure_runner(self):
        """Arm the 100ms transport tick if it isn't already running. Called
        whenever the transport engages play/rec/ff/rew; the tick drops itself
        (returns False, clearing this id) once the transport returns to stop,
        so a quiet/stopped window schedules no idle wakeups."""
        if not self._closed and self._runner_id is None:
            self._runner_id = GLib.timeout_add(100, self._runner)

    def _toggle_play(self):
        if self.transport in ("play", "rec"):
            self._stop_transport()
        else:
            self._on_play()
            return
        self.refresh()

    def _on_play(self, *_):
        # Switching transport mid-record would abandon the in-progress take
        # (rec_start never committed) — ignore Play while recording, matching
        # the lane/ruler click-while-recording guards. Stop/Record ends a take.
        if self.transport == "rec":
            return
        if self.pos >= self.length - 0.05:
            self.pos = 0.0          # play from the top rather than not at all
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
        self.engine.stop()      # nothing sounds while scrubbing
        if self.transport == "rew":
            self._ensure_runner()
        self.refresh()

    def _on_ff(self, *_):
        # ignore transport change while recording (see _on_play) so an
        # accidental Fast-Forward can't silently abort the in-progress take
        if self.transport == "rec":
            return
        self.transport = "stop" if self.transport == "ff" else "ff"
        self.engine.stop()      # nothing sounds while scrubbing
        if self.transport == "ff":
            self._ensure_runner()
        self.refresh()

    def _on_rec(self, *_):
        if self.transport == "rec":
            self._stop_transport()
            return
        self._begin_record()
        self.refresh()

    def _begin_record(self):
        """Roll. With Count-in on, the click counts a bar in FIRST.

        The count-in is not a separate timer any more. The renderer is simply
        started one bar EARLY — its click grid runs through negative time as
        readily as positive — so the count is in the song's own tempo by
        construction, and the capture, which starts at the same moment, has that
        whole bar to get the input open before anything has to be in time. The
        committed clip skips the count-in by starting that far into its take
        (see clip_make's `off`), which is the same mechanism a split uses."""
        self.transport = "rec"
        self.rec_start = self.pos
        self._preroll = self.sec_per_bar() if self.countin else 0.0
        self._start_audio(self.pos - self._preroll)
        self._start_capture()
        self._ensure_runner()

    def _set_capture_device(self, dev):
        self._cap_device = dev
        label = dict(capture_devices()).get(dev, dev)
        self._flash(_t("Input: %s") % label)
        self._save_soon()

    def _armed_tracks(self):
        """The tracks a take would land on. Every track records, so this is
        simply the armed ones — there is no second thing to have set."""
        return [i for i, tk in enumerate(self.tracks) if tk["armed"]]

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
        """Begin a take if anything is armed.

        A silent return here is the single most likely reason somebody reports
        that recording does not work, so the one case that does nothing says
        why it did nothing."""
        if not self._armed_tracks():
            self._flash(_t("Arm a track to record"))
            return
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(TAKES_DIR, "take-%s.wav" % stamp)
        ok, msg = self.recorder.start(self._capture_device(), path,
                                      monitor=self.monitor)
        if not ok:
            self._flash(msg)
        elif self.monitor and self.recorder.monitor_failed:
            # Say it. Waiting to hear yourself through speakers that are never
            # going to carry it is a minute wasted per take, and there is
            # nothing on screen to distinguish it from a dead microphone.
            self._flash(_t("Recording — but this computer's output is busy, "
                           "so there is nothing to monitor through"))

    def _finish_capture(self):
        """End the take and return its WAV path, or None if nothing was
        captured (nothing armed, no recorder, or a dead/busy input)."""
        died = self.recorder.failed_early()
        path = self.recorder.stop()
        if died and path is None:
            self._flash(_t("Nothing was recorded — try another input"))
        elif path is not None and wav_peak(path) == 0:
            # A take of pure digital silence is the signature of an input that
            # opened but is muted or turned all the way down. The file is a
            # valid WAV of the right length, so every other check passes and
            # the lane looks recorded — say plainly that nothing came in
            # rather than leaving a clip that plays nothing.
            self._flash(_t("The input recorded silence — check the "
                           "microphone level"))
        return path

    def _stop_transport(self, seek_to=None):
        wav = self._finish_capture() if self.transport == "rec" else None
        self.engine.stop()
        armed = self._armed_tracks()
        committing = (self.transport == "rec" and self.rec_start is not None
                      and self.pos - self.rec_start > 0.2 and armed)
        if committing:
            if wav:
                # a take that lands is a step Undo can walk back
                self._remember("Record")
                for i in armed:
                    self.tracks[i]["clips"].append(
                        clip_make(self.rec_start, self.pos, wav,
                                  getattr(self, "_preroll", 0.0)))
            else:
                # A clip with no take behind it can never make a sound.
                # Committing one leaves the tape LOOKING recorded on a machine
                # with no working input, with nothing to explain the silence.
                self._flash(_t("Nothing was recorded — try another input"))
        self.transport = "stop"
        self.rec_start = None
        self._preroll = 0.0
        if seek_to is not None:
            self.pos = seek_to
        self._save()
        self.refresh()

    def _toggle(self, _b, i, key):
        if self._loading:
            return
        self.tracks[i][key] = not self.tracks[i][key]
        self._save()
        self._engine_changed()
        self.refresh()

    # ---- clip edits ---------------------------------------------------------
    def _toggle_countin(self):
        self.countin = not self.countin
        self._save()
        self._flash(_t("Count-in on") if self.countin else _t("Count-in off"))

    def _toggle_monitor(self, *_):
        """Turn hearing the input on or off — and do it to the take in
        progress, not only to the next one.

        A toggle that waited for the next take would be useless in the one
        moment it is reached for in a hurry, which is a microphone howling into
        the speakers it is being monitored through."""
        self.monitor = not self.monitor
        self._save()
        if self.transport == "rec":
            if self.monitor:
                self.recorder.start_monitor()
            else:
                self.recorder.stop_monitor()
        self._flash(_t("Monitoring on") if self.monitor
                    else _t("Monitoring off"))
        self.refresh()

    def _toggle_tool(self, *_):
        self._set_tool(TOOL_SELECT if self.tool == TOOL_CUT else TOOL_CUT)

    def _toggle_snap(self, *_):
        """Off, or back on at bars — the two ends of the grid control, for
        when the answer is "stop rounding my edits" rather than "round them
        differently"."""
        self.snap = DEFAULT_SNAP if self.snap == SNAP_FREE else SNAP_FREE
        self._update_snap_btn()
        self._save_soon()
        for lane in getattr(self, "lanes", []):
            lane.queue_draw()

    def _zoom_to_clip(self, *_):
        c = self.sel_clip()
        if c is not None:
            self.zoom_to(c["s"], c["e"])

    # ---- the clipboard, which holds CLIPS ---------------------------------
    def _copy_clip(self, *_):
        c = self.sel_clip()
        if c is None:
            return
        self._clipboard = clip_copy(c)
        self._flash(_t("Clip copied"))

    def _cut_clip(self, *_):
        c = self.sel_clip()
        if c is None:
            return
        self._clipboard = clip_copy(c)
        self._delete_selected(name="Cut Clip")

    def _paste_clip(self, *_):
        """Drop the copied clip onto the selected track at the playhead.

        The take itself is never copied — the pasted clip points at the same
        WAV, exactly as a repeat does — so pasting is instant however long the
        recording is, and there is only ever one file on the disk per take."""
        src = self._clipboard
        if src is None:
            return
        ti = self.sel[0] if self.sel else 0
        ti = max(0, min(len(self.tracks) - 1, ti))
        span = src["e"] - src["s"]
        at = self.snap_time(self.pos)
        if at + span > self.length + 1e-6:
            self._flash(_t("There is no room left in the pattern for that"))
            return
        self._remember("Paste Clip")
        fresh = clip_make(at, at + span, src.get("wav"), src.get("off", 0.0),
                          src.get("gain", 1.0), src.get("fin", 0.005),
                          src.get("fout", 0.005))
        self.tracks[ti]["clips"].append(fresh)
        self.sel = (ti, fresh)
        self._save()
        self._engine_changed()
        self._sync_editor()
        self.refresh()

    def _delete_selected(self, *_a, name="Remove Clip"):
        """Take the selected clip off its lane. The take's WAV stays on the
        disk, so Undo gets the sound back and not just the block."""
        c = self.sel_clip()
        if c is None:
            return
        ti = self.sel[0]
        self._remember(name)
        self.tracks[ti]["clips"] = [x for x in self.tracks[ti]["clips"]
                                    if x is not c]
        self._validate_sel()
        self._save()
        self._engine_changed()
        self._sync_editor()
        self.refresh()

    def _nudge_selected(self, steps, fine=False):
        """Move the selected clip along its lane from the keyboard.

        One grid step per press, or a hundredth of a second with Shift — which
        is the resolution that matters once a take is being lined up against
        another one by ear rather than against the bars."""
        c = self.sel_clip()
        if c is None:
            return
        step = 0.01 if fine else (self.snap_seconds() or self.sec_per_bar())
        span = c["e"] - c["s"]
        s = max(0.0, min(self.length - span, c["s"] + steps * step))
        if abs(s - c["s"]) < 1e-9:
            return
        self._remember("Nudge")
        c["s"], c["e"] = s, s + span
        self._sync_editor()
        self.clip_changed()

    def _nudge_track(self, rows):
        """Move the selected clip up or down onto another lane."""
        c = self.sel_clip()
        if c is None:
            return
        ti = self.sel[0]
        if not (0 <= ti + rows < len(self.tracks)):
            return
        self._remember("Move to Another Track")
        self.move_clip_to_track(ti, c, ti + rows)
        self._sync_editor()
        self.clip_changed()

    def _clips_under_playhead(self):
        """[(track index, clip index)] the playhead is inside, strictly — a
        clip is not "under" the playhead at its own start or end, or splitting
        would make a zero-length piece."""
        out = []
        for i, tk in enumerate(self.tracks):
            for j, c in enumerate(tk["clips"]):
                st, en, _w = clip_parts(c)
                if st < self.pos < en:
                    out.append((i, j))
        return out

    @staticmethod
    def _cut_in_two(tk, c, at):
        """Replace clip `c` on track `tk` with the two halves either side of
        `at`. Returns the pair, or None if the cut falls outside the clip.

        BOTH HALVES KEEP THE SAME TAKE FILE, and the right-hand one starts that
        much further into it (see clip_offset). No audio is copied, no audio is
        deleted, and either half can be trimmed straight back out to where it
        was — which is what makes cutting something to try rather than something
        to be sure about. The clip's own level goes to both halves; its fades
        stay on the outer ends, because a fade-in belongs to the start of the
        take and not to the start of every piece of it."""
        st, en, wav = clip_parts(c)
        if not (st < at < en):
            return None
        off = clip_offset(c)
        g = c.get("gain", 1.0)
        left = clip_make(st, at, wav, off, g, c.get("fin", 0.005), 0.005)
        right = clip_make(at, en, wav, off + (at - st), g,
                          0.005, c.get("fout", 0.005))
        i = next((k for k, x in enumerate(tk["clips"]) if x is c), None)
        if i is None:
            return None
        tk["clips"][i:i + 1] = [left, right]
        return left, right

    def cut_clip_at(self, ti, at):
        """The CUT tool: cut whatever is under `at` on one track, on the grid
        (or exactly there, with the grid off)."""
        if not (0 <= ti < len(self.tracks)):
            return
        at = self.snap_time(at)
        tk = self.tracks[ti]
        c = next((x for x in reversed(tk["clips"])
                  if x["s"] < at < x["e"]), None)
        if c is None:
            self._flash(_t("There is no clip here to cut"))
            return
        # A cut within a hair of an end makes a piece too short to see, let
        # alone grab — which reads as the knife having done nothing.
        if min(at - c["s"], c["e"] - at) < Lane.MIN_CLIP:
            self._flash(_t("That is too close to the end of the clip to cut"))
            return
        self._remember("Cut")
        pair = self._cut_in_two(tk, c, at)
        if pair:
            self.sel = (ti, pair[1])
        self._save()
        self._sync_editor()
        self.refresh()

    def _split_at_playhead(self):
        """Cut every clip the playhead is inside into two, across all tracks.

        The knife cuts one clip where it is clicked; this cuts the whole
        arrangement on one line, which is the other thing anybody wants — it is
        how a section is lifted out of eight tracks at once without eight
        separate cuts that do not quite line up."""
        hits = self._clips_under_playhead()
        if not hits:
            self._flash(_t("Move the playhead over a clip to split it"))
            return
        self._remember("Split")
        pos = self.pos
        for tk in self.tracks:
            for c in [x for x in tk["clips"] if x["s"] < pos < x["e"]]:
                self._cut_in_two(tk, c, pos)
        self._save()
        self._sync_editor()
        self.refresh()
        self._flash(_t("Split %d clip") % len(hits) if len(hits) == 1
                    else _t("Split %d clips") % len(hits))

    def move_clip_to_track(self, from_ti, c, to_ti):
        """Take clip `c` off track `from_ti` and put it on `to_ti`.

        Called while a clip is being DRAGGED, so it must be cheap and must not
        touch the selection's identity: the clip object is the same object on
        its new lane, which is why the drag can carry on through the move
        without the pointer letting go of anything."""
        to_ti = max(0, min(len(self.tracks) - 1, int(to_ti)))
        if to_ti == from_ti:
            return False
        src = self.tracks[from_ti]["clips"]
        i = next((k for k, x in enumerate(src) if x is c), None)
        if i is None:
            return False
        src.pop(i)
        self.tracks[to_ti]["clips"].append(c)
        self.sel = (to_ti, c)
        # the dragging lane keeps the pointer grab, so BOTH have to be redrawn
        # by hand — GTK will only send the one under the pointer an expose
        for k in (from_ti, to_ti):
            try:
                self.lanes[k].queue_draw()
            except (AttributeError, IndexError):
                pass
        return True

    def _loop_at_playhead(self):
        """Repeat each clip under the playhead, end to end, to the end of the
        pattern.

        The repeats are real clips on the grid rather than a hidden flag: they
        can be seen, moved past, and deleted one at a time, and playback needs
        to know nothing new. A clip already reaching the end has nothing to
        repeat into and is left alone."""
        hits = self._clips_under_playhead()
        if not hits:
            self._flash(_t("Move the playhead over a clip to loop it"))
            return
        made = 0
        self._remember("Loop")
        for i, j in hits:
            tk = self.tracks[i]
            src = tk["clips"][j]
            st, en, wav = clip_parts(src)
            off = clip_offset(src)
            span = en - st
            if span <= 0.05:
                continue
            at = en
            # A cap as well as the pattern end: a very short clip on a long
            # pattern would otherwise make thousands of copies.
            while at + span <= self.length and made < 256:
                # a REPEAT of what is there — the point of building a loop is
                # that the copy plays the same thing, not an empty block
                tk["clips"].append(clip_make(at, at + span, wav, off,
                                             src.get("gain", 1.0),
                                             src.get("fin", 0.005),
                                             src.get("fout", 0.005)))
                at += span
                made += 1
        if not made:
            self._flash(_t("There is no room left in the pattern to repeat it"))
            return
        self._save()
        self.refresh()
        self._flash(_t("Repeated %d time") % made if made == 1
                    else _t("Repeated %d times") % made)

    def _on_track_name(self, entry, i):
        """Rename a track from what is typed on its head."""
        if self._loading or not (0 <= i < len(self.tracks)):
            return
        name = entry.get_text().strip()[:48]
        self.tracks[i]["name"] = name or ("Track %d" % (i + 1))
        try:
            entry.set_tooltip_text(self.tracks[i]["name"])
            self.strips[i]["name"].set_text(self.tracks[i]["name"])
        except Exception:
            pass
        self._save_soon()

    def _clear_track(self, _b, i):
        """Confirm before deleting just this one track's takes (destructive, no
        undo — mirrors 'Clear All Takes'). Leaves the other tracks alone."""
        n = len(self.tracks[i]["clips"])
        if n == 0:
            return
        self._do_clear_track(i)

    def _do_clear_track(self, i):
        """Wipe one track's takes — runs only after the confirm is accepted."""
        if not self.tracks[i]["clips"]:
            return
        self._remember("Remove Clips")
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
        self._engine_changed()
        self.refresh()

    def _on_master(self, scale):
        if self._loading:
            return
        self.master = int(scale.get_value())
        self._save_soon()
        self._engine_changed()
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
        self._save_soon()
        self._engine_changed()
        self._sync_editor()
        self.refresh()

    def _toggle_metro(self, *_):
        if self._loading:
            return
        self.metronome = not self.metronome
        self._save()
        self._engine_changed()
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

    # ---- the grid ---------------------------------------------------------
    # EVERY GESTURE THAT PLACES A MOMENT GOES THROUGH THESE TWO. Moving a clip,
    # trimming an end, cutting one in two, dragging a loop out, seeking, and
    # nudging from the keyboard all ask snap_time() where they may land, which
    # is the only reason one control on the timeline bar can honestly claim to
    # govern all of them.
    def snap_seconds(self):
        """How long one grid step is, in seconds. 0.0 when SNAP is FREE."""
        if self.snap <= 0:
            return 0.0
        return self.snap * self.sec_per_beat()

    def snap_time(self, t):
        """A moment rounded to the grid — or left exactly alone when the grid
        is off, which is the whole point of having FREE in the list."""
        step = self.snap_seconds()
        if step <= 0:
            return max(0.0, min(self.length, t))
        return max(0.0, min(self.length, round(t / step) * step))

    def snap_span(self, t0, t1):
        """A dragged stretch, snapped at both ends and never shorter than one
        grid step (one bar when the grid is off, since a loop of no length is
        not a loop). Never runs off the end of the tape."""
        step = self.snap_seconds() or self.sec_per_bar()
        s = self.snap_time(min(t0, t1))
        e = self.snap_time(max(t0, t1))
        if e - s < step - 1e-9:
            e = min(self.length, s + step)
            s = max(0.0, e - step)
        return s, e

    def remove_part(self, i, t):
        """Take the clip under `t` off track `i` (right-click). Undo restores
        it — and a captured take's WAV is left on disk, so undo gets the sound
        back too, not just the region."""
        tk = self.tracks[i]
        keep = [c for c in tk["clips"]
                if not (clip_parts(c)[0] <= t <= clip_parts(c)[1])]
        if len(keep) == len(tk["clips"]):
            return
        self._remember()
        tk["clips"] = keep
        self._validate_sel()
        self._save()
        self._engine_changed()
        self._sync_editor()
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
        confirm first; Undo restores the prior clip geometry. An empty tape
        just cycles."""
        cur_bars = self.bars_total()
        nxt_bars = next((b for b in BAR_CHOICES if b > cur_bars), BAR_CHOICES[0])
        nxt = nxt_bars * self.sec_per_bar()
        if nxt < self.length:
            lost = sum(1 for tk in self.tracks
                       for (s, e, _w) in map(clip_parts, tk["clips"])
                       if e > nxt + 0.001)
            if lost:
                self._set_length(nxt)
                return
        self._set_length(nxt)

    def _set_length(self, v):
        """Adopt a new pattern length, clamping takes and the playhead to it."""
        self._remember()   # shortening trims takes — make that a step back
        self.length = _clampf(v, 10.0, 600.0, DEFAULT_LEN)
        for tk in self.tracks:
            trimmed = []
            for c in tk["clips"]:
                s, e, _w = clip_parts(c)
                if s >= self.length - 0.001:
                    continue
                e = min(e, self.length)
                if e - s > 0.001:
                    # the clip is trimmed IN PLACE, so its take, its level and
                    # its fades come through the change; rebuilt as a bare
                    # (start, end) it would lose all of them, and shortening
                    # the tape would silently empty every clip that survived
                    c["e"] = e
                    trimmed.append(c)
            tk["clips"] = trimmed
        self.pos = min(self.pos, self.length)
        if self.rec_start is not None:
            self.rec_start = min(self.rec_start, self.length)
        # the tape is a different length, so the window onto it is a different
        # fraction of it and the scrollbar's map has to be redrawn
        self._clamp_view()
        self._after_view_change()
        self._validate_sel()
        self._update_length_btn()
        self._save()
        self._engine_changed()
        self._sync_editor()
        self.refresh()

    # ================= bulk track ops (menu helpers) =================
    def _arm_all(self, on):
        self._remember("Arm All Tracks" if on else "Disarm All Tracks")
        for tk in self.tracks:
            tk["armed"] = bool(on)
        self._save()
        self.refresh()

    def _mute_all(self, on):
        self._remember("Mute All Tracks" if on else "Unmute All Tracks")
        for tk in self.tracks:
            tk["muted"] = bool(on)
        self._save()
        self.refresh()

    def _solo_all(self, on):
        self._remember("Clear Solo" if not on else None)
        for tk in self.tracks:
            tk["solo"] = bool(on)
        self._save()
        self.refresh()

    def _clear_takes_confirm(self):
        """Confirm before wiping every recorded take; Undo restores clips."""
        if not sum(len(tk["clips"]) for tk in self.tracks):
            return
        self._clear_takes()

    def _clear_takes(self):
        if self.transport == "rec":
            self._stop_transport()
        self._remember("Remove Every Clip")
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
                ("Export as Audio…",
                 None if self._export else self._export_audio),
                nbapp.SEP,
                ("Close    Esc", self.close),
            ]
        if name == "Edit":
            # The SHARED undo builder every other editor uses, so all of them
            # word (and key) Undo/Redo identically: Redo prints Ctrl+Shift+Z
            # like the rest of the OS (Ctrl+Y still works), and each entry
            # names the action it takes back.
            #
            # Then Cut/Copy/Paste — which mean CLIPS here, not text. There is
            # no text widget in this window for the base ones to act on, and a
            # take that belongs on the second chorus as well as the first is
            # the commonest thing there is to want to copy.
            sel = self.sel_clip() is not None
            return nbapp.undo_menu_items(self.history) + [
                nbapp.SEP,
                ("Cut Clip    Ctrl+X", self._cut_clip if sel else None),
                ("Copy Clip    Ctrl+C", self._copy_clip if sel else None),
                ("Paste Clip at the Playhead    Ctrl+V",
                 self._paste_clip if self._clipboard else None),
                ("Remove Clip    Delete",
                 self._delete_selected if sel else None),
            ]
        if name == "Transport":
            # a leading check marks the metronome when it is on, so the menu
            # shows its state (matching the View menus across the DE). The
            # word is translated BEFORE the mark is glued on: the menu builder
            # looks the whole label up, and "    Metronome" matches no catalog
            # key, so a marked item would sit in English among translated ones.
            metro = "✓ " if self.metronome else "    "
            cin = "✓ " if self.countin else "    "
            loop = "✓ " if self.loop_on else "    "
            mon = "✓ " if self.monitor else "    "
            return [
                ("Play    Space", lambda: self._on_play()),
                ("Stop", lambda: self._on_stop()),
                ("Record", lambda: self._on_rec()),
                nbapp.SEP,
                ("Rewind", lambda: self._on_rew()),
                ("Fast Forward", lambda: self._on_ff()),
                nbapp.SEP,
                ("Return to Zero", lambda: self._stop_transport(0)),
                (loop + _t("Loop"), lambda: self._toggle_loop()),
                ("Loop the Selected Clip", self._loop_selected),
                nbapp.SEP,
                (metro + _t("Metronome"), lambda: self._toggle_metro()),
                (cin + _t("Count-in Before Recording"),
                 lambda: self._toggle_countin()),
                (mon + _t("Monitor the Input While Recording"),
                 lambda: self._toggle_monitor()),
            ]
        if name == "View":
            cut = "✓ " if self.tool == TOOL_CUT else "    "
            grid = next((n for (v, n) in SNAP_CHOICES if v == self.snap), "BAR")
            return [
                ("Zoom In    +", lambda: self._zoom_step(ZOOM_STEP)),
                ("Zoom Out    −", lambda: self._zoom_step(1.0 / ZOOM_STEP)),
                ("Show the Whole Song    Ctrl+0", lambda: self.zoom_fit()),
                ("Zoom to the Looped Bars", self._zoom_to_loop),
                ("Zoom to the Selected Clip",
                 self._zoom_to_clip if self.sel_clip() else None),
                nbapp.SEP,
                (cut + _t("Cut Tool    C"), self._toggle_tool),
                # the grid's current setting is IN the label rather than a row
                # of eight ticked items: it is one value out of six and the
                # menu is not where it is normally changed
                ("Snap: %s" % _t(grid), self._cycle_snap),
                ("Snap Off" if self.snap != SNAP_FREE else "Snap to Bars",
                 self._toggle_snap),
            ]
        if name == "Track":
            under = bool(self._clips_under_playhead())
            sel = self.sel_clip() is not None
            return [
                ("Open Clip in the Editor", self._open_selected if sel else None),
                ("Duplicate Clip", self._duplicate_clip if sel else None),
                nbapp.SEP,
                ("Split at Playhead    S",
                 self._split_at_playhead if under else None),
                ("Repeat Clip to the End",
                 self._loop_at_playhead if under else None),
                nbapp.SEP,
                ("Arm All Tracks", lambda: self._arm_all(True)),
                ("Disarm All Tracks", lambda: self._arm_all(False)),
                nbapp.SEP,
                ("Mute All Tracks", lambda: self._mute_all(True)),
                ("Unmute All Tracks", lambda: self._mute_all(False)),
                ("Clear Solo", lambda: self._solo_all(False)),
                nbapp.SEP,
                ("Remove Every Clip…", self._clear_takes_confirm
                 if sum(len(tk["clips"]) for tk in self.tracks) else None),
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
            # Choosing a device is only half of it: a track still has to be
            # ARMED, or Record captures from the input just picked here onto
            # nothing at all. That was easy to miss when there was a second
            # thing called an input as well; it is worth saying even now.
            mon = "✓ " if self.monitor else "    "
            items.append((mon + _t("Monitor the Input While Recording"),
                          self._toggle_monitor))
            items.append(("An armed track records from this input", None))
            return items
        return super().menu_items(name)

    # ================= the loop =================
    def _set_loop(self, start, end, on=True):
        """Set the stretch of the arrangement to go round and round."""
        start = max(0.0, min(self.length, min(start, end)))
        end = max(0.0, min(self.length, max(start, end)))
        if end - start < 0.05:
            self.loop_on = False
        else:
            self.loop_s, self.loop_e = start, end
            self.loop_on = bool(on)
        self._save_soon()
        self._engine_changed()
        self.refresh()

    def _toggle_loop(self, *_):
        """Turn the loop on or off, choosing a sensible one if none is set."""
        if not self.loop_on and self.loop_e - self.loop_s < 0.05:
            c = self.sel_clip()
            if c is not None:
                self._set_loop(c["s"], c["e"])
                return
            self._flash(_t("Drag across the ruler to choose the bars to loop"))
            return
        self.loop_on = not self.loop_on
        self._save_soon()
        self._engine_changed()
        self.refresh()

    def _loop_selected(self):
        """Loop exactly the bars the selected clip covers."""
        c = self.sel_clip()
        if c is None:
            self._flash(_t("Select a clip first"))
            return
        self._set_loop(c["s"], c["e"])

    # ================= clip commands =================
    def _open_selected(self):
        if self.sel_clip() is not None:
            self._set_view("edit")

    def _duplicate_clip(self):
        """Copy the selected clip to the first free bar after it.

        The commonest edit there is in a song built out of loops: write four
        bars, then have them again. It lands right after the original when
        there is room, which is what a repeat means."""
        c = self.sel_clip()
        if c is None:
            return
        ti = self.sel[0]
        tk = self.tracks[ti]
        span = c["e"] - c["s"]
        at = c["e"]
        if at + span > self.length + 1e-6:
            self._flash(_t("There is no room left in the pattern to repeat it"))
            return
        self._remember("Duplicate Clip")
        fresh = clip_make(at, at + span, c.get("wav"), c.get("off", 0.0),
                          c.get("gain", 1.0), c.get("fin", 0.005),
                          c.get("fout", 0.005))
        tk["clips"].append(fresh)
        self.sel = (ti, fresh)
        self._save()
        self._engine_changed()
        self._sync_editor()
        self.refresh()

    # ================= export =================
    def _export_audio(self):
        """Render the whole song to a .wav under Documents.

        The same renderer that plays it, run flat out into a file instead of a
        sound card — so the export is what was heard, including the reverb tail
        past the last note. It runs on a thread with the window still live; the
        status line carries the progress."""
        if self._export is not None:
            return
        if self.is_empty():
            self._flash(_t("There is nothing in the arrangement to export"))
            return
        try:
            os.makedirs(PROJ_DIR, exist_ok=True)
        except OSError:
            pass
        base = os.path.splitext(os.path.basename(self._path or "song"))[0]
        path = nbpicker.save_file(self, title="Export as Audio",
                                  start_dir=PROJ_DIR,
                                  suggested_name=base + ".wav",
                                  patterns=("*.wav",), default_ext=".wav")
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += ".wav"
        song = self._song()
        song["metronome"] = False       # a click is for playing to, not keeping
        state = {"pct": 0.0, "done": False, "ok": False, "path": path}
        self._export = state

        def work():
            try:
                state["ok"] = nbsynth.render_wav(
                    song, path,
                    progress=lambda p: state.__setitem__("pct", p)) is not None
            except Exception:
                state["ok"] = False
            state["done"] = True

        threading.Thread(target=work, daemon=True).start()
        GLib.timeout_add(250, self._export_tick)

    def _export_tick(self):
        if self._closed:
            self._export = None
            return False
        state = self._export
        if state is None:
            return False
        if not state["done"]:
            self._flash(_t("Exporting %d%%") % int(state["pct"] * 100))
            return True
        self._export = None
        if state["ok"]:
            self._flash(_t("Exported to %s") % os.path.basename(state["path"]))
        else:
            self._flash(_t("Couldn't export the audio"))
        return False

    # ================= animation =================
    def _runner(self):
        if self._closed:
            self._runner_id = None
            return False
        t = self.transport
        if t in ("play", "rec"):
            # nbmotion-inventory: content.sequencer
            #
            # THE PLAYHEAD MUST STAY LINEAR, AND THAT IS A RULE, NOT AN
            # OVERSIGHT. Every other state change in this OS animates with a
            # slight spring (nbmotion.ARRIVE) — the view's own travel a few
            # hundred lines up does. A playhead may not: it is a PHYSICAL
            # QUANTITY, the position the sound card has actually reached, and
            # easing it would draw the head somewhere the audio is not.
            # Overshoot would put it past a note before the note sounds. See
            # PAPER-PHYSICS §D2; do not "smooth" this.
            #
            # The PLAYHEAD FOLLOWS THE SOUND. It used to be advanced by this
            # timer, which meant the picture and the audio were two independent
            # clocks that drifted apart over a long take; now the position comes
            # from the pipeline, so the head is over the note being heard. With
            # no sound engine the free-running clock below keeps the visual
            # sequencer working exactly as before.
            if self.engine.available:
                self.pos = self.engine.position()
            else:
                now = time.monotonic()
                p = self._free_pos + (now - (self._free_t0 or now))
                # with no sound engine the loop still has to come round, or the
                # picture and the transport disagree about what is playing
                if (self.transport == "play" and self.loop_on
                        and self.loop_e - self.loop_s > 0.05 and p > self.loop_e):
                    span = self.loop_e - self.loop_s
                    p = self.loop_s + (p - self.loop_s) % span
                self.pos = p
            if (self.transport == "rec" and self.rec_start is not None
                    and self.pos < self.rec_start - 0.001):
                # counting in: the click is already sounding a bar ahead of the
                # punch-in point, but nothing is being recorded yet, so the head
                # waits where the take will begin instead of running backwards
                self.pos = self.rec_start
                self.refresh()
                return True
            self.pos = max(0.0, min(self.length, self.pos))
            self.tick += 1
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

    # ================= audio =================
    def _update_audio_lbl(self):
        """Keep the status-bar sound note current (neutral when absent).

        Says it in the user's terms: the renderer is a part of this program,
        and someone who just pressed Play only needs to know whether they will
        hear anything.

        A machine that cannot render the arrangement as fast as it plays gets a
        note of its own. It is not a fault to be hidden — the sound really does
        break up, and knowing that turning the master effects off will fix it is
        the difference between a slow computer and a broken program."""
        try:
            if self.engine.available and self.engine.bypassed:
                self.audio_lbl.set_text(
                    _t("Effects off while playing — this computer can't "
                       "render them in time"))
                return
            if self.engine.available and self.engine.underruns > 3:
                self.audio_lbl.set_text(_t("Sound breaking up"))
                return
            if self.engine.available:
                txt = _t("Sound ready")
            elif self.engine.failed or not GST_OK:
                txt = _t("No sound on this computer")
            else:
                txt = ""
            self.audio_lbl.set_text(txt)
        except Exception:
            pass

    def is_empty(self):
        """True while the arrangement holds no clips at all (first run)."""
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
        """How loud this lane IS right now, read off the renderer.

        Not an animation keyed to the gain fader — the meters used to bounce
        identically whether anything was sounding or not, which made the one
        control someone uses to check a level the one control that lied. This
        is the peak of the samples the mixdown actually summed for this track,
        including a recorded take: the engine mixes takes itself now, so a Mic
        lane meters like every other lane instead of staying dark."""
        if self.transport not in ("play", "rec") or not self.engine.available:
            return 0.0
        peaks = self.engine.track_peaks()
        if i >= len(peaks):
            return 0.0
        # a little ballistics, so a meter reads as a level and not as a strobe:
        # jump to a new peak at once, fall back gently
        prev = self._vu_hold[i] if i < len(self._vu_hold) else 0.0
        v = max(0.0, min(1.0, peaks[i]))
        v = v if v >= prev else prev * 0.72 + v * 0.28
        if i < len(self._vu_hold):
            self._vu_hold[i] = v
        return v

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
                    nbicons.set_image(img, icon, 18, icon_col)
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
        _cls(self.loop_btn, "on", self.loop_on)
        beat_len = 60.0 / self.bpm if self.bpm > 0 else 0.0
        beat_on = False
        if beat_len > 0:
            beat_on = ((self.pos % beat_len) / beat_len) < 0.5
        if not self.metronome:
            self.metro_dot.set_color(VU_OFF)
        elif playing and beat_on:
            self.metro_dot.set_color(RED)
        else:
            self.metro_dot.set_color(MUTED)
        # MONITOR, lit whenever it is armed to run and RED while it actually
        # is — "on" and "running" are different states and only one of them
        # means something is coming out of the speakers.
        _cls(self.mon_btn, "on", self.monitor)
        if self.transport == "rec" and self.recorder.monitoring:
            self.mon_dot.set_color(RED)
        elif self.monitor:
            self.mon_dot.set_color(MUTED)
        else:
            self.mon_dot.set_color(VU_OFF)
        self.in_vu.set_level(self.recorder.level())
        # master readout — set from its own value, not the transport, so it
        # only rewrites (and only recomputes 20·log10) when the user actually
        # moves the fader, never on every 100ms tick.
        if r.get("master") != self.master:
            r["master"] = self.master
            mv = self.master
            if mv <= 0:
                self.master_lbl.set_text(_t("0% · off"))
            else:
                dbi = int(round(20 * math.log10(mv / 100.0)))
                db_txt = "0" if dbi == 0 else "%+d" % dbi
                self.master_lbl.set_text("%d%% · %s dB" % (mv, db_txt))
        # Master VU: the peak of the block the sound card is being handed, so
        # the meter reads the mix that is actually leaving the machine.
        pl, pr = (self.engine.peaks() if playing and self.engine.available
                  else (0.0, 0.0))
        self.master_vu.set_level(max(pl, pr))
        self.master_vu2.set_level(pl)
        self.master_vu3.set_level(pr)
        if r.get("master2") != self.master:
            r["master2"] = self.master
            self.master_lbl2.set_text(self.master_lbl.get_text())
        _cls(self.fx_btn, "on", self.fx)
        self.dly_time_btn.set_label(
            dict(self.DELAY_TIMES).get(self.dly_time, "1/8"))
        # the three views
        for key, _label in self.VIEWS:
            _cls(self.view_btns[key], "on", self.view == key)
        # per-track
        for i, tk in enumerate(self.tracks):
            tw = self.track_widgets[i]
            st = self.strips[i]
            _cls(tw["head"], "armed", tk["armed"])
            _cls(tw["mute"], "on", tk["muted"])
            _cls(tw["solo"], "on", tk["solo"])
            _cls(tw["arm"], "on", tk["armed"])
            g = tk["gain"]
            # A real conversion against UNITY, the same one the master uses.
            # This was `(g - 90) / 6` — a straight-line guess referenced to 90,
            # so a fader sitting exactly on unity read "+2 dB" and the number
            # disagreed with the tick right under it.
            if g <= 0:
                tw["gainlbl"].set_text("−∞")
            else:
                gdb = int(round(20 * math.log10(g / float(GAIN_UNITY))))
                tw["gainlbl"].set_text("0 dB" if gdb == 0 else "%+d dB" % gdb)
            lvl = self._vu_level(tk, i)
            tw["vu"].set_level(lvl)
            st["vu"].set_level(lvl)
            st["gainlbl"].set_text(tw["gainlbl"].get_text())
            _cls(st["mute"], "on", tk["muted"])
            _cls(st["solo"], "on", tk["solo"])
            # per-track clear is only meaningful once a clip exists
            has_clips = bool(tk["clips"])
            tw["clr"].set_sensitive(has_clips)
            tw["clr"].set_tooltip_text(
                _t("Remove this track's clips") if has_clips else
                _t("This track has no clips to remove."))
        # status bar summaries
        # every status string is translated HERE, at the point it is applied:
        # refresh() rewrites these labels with a bare set_text(), so returning
        # English snapped the status bar back to English on a localised install
        # the moment anything changed.
        if self.loop_on and self.loop_e - self.loop_s > 0.05:
            b1, _x = self.bar_beat_at(self.loop_s)
            b2, _x = self.bar_beat_at(max(0.0, self.loop_e - 0.001))
            self.view_ctx_extra = _t("Looping bars %d to %d") % (b1, b2)
        else:
            self.view_ctx_extra = ""
        armed = [tk["name"] for tk in self.tracks if tk["armed"]]
        if not armed:
            armed_txt = _t("No tracks armed")
        elif len(armed) <= 2:
            armed_txt = _t("%s armed") % ", ".join(armed)
        else:
            armed_txt = _t("%d tracks armed") % len(armed)
        self.armed_lbl.set_text(armed_txt)
        n = sum(len(tk["clips"]) for tk in self.tracks)
        # the counted forms are left SUBSTITUTED, not _t()d: the catalogs carry
        # "%d clip%s" as a "singular|plural" pair that nbi18n rebuilds from the
        # finished string, and _t() on that key would hand back the raw pair.
        if n == 0:
            self.takes_lbl.set_text(_t("No clips"))
        else:
            self.takes_lbl.set_text("%d clip%s" % (n, "" if n == 1 else "s"))
        self.specs_lbl.set_text(
            "%s · 16-bit · %d BPM · %s"
            % (SR_KHZ, self.bpm, _fmt_len(self.length)))
        # sound-engine status note
        self._update_audio_lbl()
        # what the middle view is currently about
        c = self.sel_clip()
        bits = []
        if c is not None:
            tk = self.tracks[self.sel[0]]
            bar1, _b = self.bar_beat_at(c["s"])
            bits.append(_t("%s from bar %d") % (tk["name"], bar1))
        if self.view_ctx_extra:
            bits.append(self.view_ctx_extra)
        self.view_ctx.set_text("   \u00b7   ".join(bits))
        # Keep a running playhead on screen before the lanes are asked to
        # draw, or a zoomed-in transport plays on past the right-hand edge and
        # everything below is drawn for a view that is about to change anyway.
        self.follow_playhead()
        # lanes — sync() repaints a lane in full only when its static content
        # (clips / view / mute / solo / length / arm / size) changed; otherwise
        # it just invalidates the thin strip the playhead sweeps, so the
        # running transport never re-rasterises all eight lanes per tick.
        for lane in self.lanes:
            lane.sync()
        # ...and the editor's own playhead, which is only on screen when the
        # transport is inside the clip being edited
        if self.view == "edit":
            self._sync_edit_playhead()

    def _sync_edit_playhead(self):
        """Per-refresh update of the editors' playhead — the lanes' sync(), one
        floor up.

        The take editor has exactly ONE piece of per-tick content: the
        playhead, and it draws that only while the transport is inside the clip
        being edited (WaveEdit._draw's guard). Everywhere else on the tape the
        canvas comes out pixel-for-pixel identical, and repainting it — as
        this did — spent ten full re-rasterisations a second on an unchanged
        picture: every waveform column of the take, every step, line and hit of
        the grid, redrawn by the CPU on hardware with no GPU, in competition
        with the renderer that is trying to keep the sound going. Play a
        four-minute arrangement with an eight-bar clip open and nearly all of
        that work produced nothing.

        So: repaint when the head is on screen, plus the one frame after it
        leaves (or the transport stops) so the last head painted is rubbed out
        rather than left behind. Every other editor change queues its own
        redraw, so nothing else depends on this path."""
        c = self.sel_clip()
        head = (self.transport in ("play", "rec") and c is not None
                and c["s"] <= self.pos <= c["e"])
        if head or self._rendered.get("edit_head"):
            self.wave_edit.queue_draw()
        self._rendered["edit_head"] = head

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

    def _menu_card(self, title, items):
        """A scrolling list of choices, as an in-window card.

        The same overlay idiom the confirm card uses, for the same reason: this
        stack has no compositor, so a popup window over a full-screen app is a
        window that can end up behind it. `items` is [(label, action)]; picking
        one runs its action and closes the card."""
        self._close_prompt()
        alloc = self.get_allocation()
        _sw, _sh = nbapp.screen_size()
        W = alloc.width if alloc.width > 1 else _sw
        H = alloc.height if alloc.height > 1 else _sh
        layer = Gtk.Fixed()
        scrim = Gtk.EventBox()
        scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        scrim.set_size_request(W, H)
        scrim.get_style_context().add_class("seqscrim")
        scrim.connect("button-press-event",
                      lambda *a: (self._close_prompt(), True)[1])
        layer.put(scrim, 0, 0)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.get_style_context().add_class("seqprompt")
        head = Gtk.Label(label=title, xalign=0)
        head.get_style_context().add_class("seqprompttitle")
        card.pack_start(head, False, False, 0)
        lst = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for label, action in items:
            b = Gtk.Button(label=label)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("cardrow")
            b.set_alignment(0.0, 0.5)
            b.connect("clicked",
                      lambda _b, a=action: (self._close_prompt(), a())[0])
            lst.pack_start(b, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        # never taller than the window it sits in — a list of every instrument
        # on a short panel would otherwise push its own card off the screen
        scroll.set_min_content_height(min(420, max(160, H - 220)))
        scroll.add(lst)
        card.pack_start(scroll, True, True, 0)
        cancel = Gtk.Button(label=_t("Cancel"))
        cancel.set_relief(Gtk.ReliefStyle.NONE)
        cancel.get_style_context().add_class("seqpromptcancel")
        cancel.set_halign(Gtk.Align.END)
        cancel.connect("clicked", lambda *_: self._close_prompt())
        card.pack_start(cancel, False, False, 0)

        card_win = Gtk.EventBox()
        card_win.add(card)
        layer.put(card_win, 0, 0)
        self._overlay.add_overlay(layer)
        layer.show_all()
        _min, nat = card_win.get_preferred_size()
        cw = nat.width if nat.width > 1 else 320
        ch = nat.height if nat.height > 1 else 300
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
            # The clipboard holds CLIPS here (there is no text widget in this
            # window for the base Cut/Copy/Paste to act on) — except while a
            # track name is being typed, where the entry must keep them.
            if not isinstance(self.get_focus(), Gtk.Entry):
                if kv in (Gdk.KEY_c, Gdk.KEY_C):
                    self._copy_clip()
                    return True
                if kv in (Gdk.KEY_x, Gdk.KEY_X):
                    self._cut_clip()
                    return True
                if kv in (Gdk.KEY_v, Gdk.KEY_V):
                    self._paste_clip()
                    return True
            if kv in (Gdk.KEY_0, Gdk.KEY_KP_0):
                self.zoom_fit()
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
                border: 1px solid #C9C4B6; border-radius: 8px;
                background: #FCFBF8; box-shadow: none; }
        .tbtn:hover { background: #EFEBE0; }
        .tbtn.on { background: #1A1916; border-color: #1A1916; }
        .recbtn { color: #C8341E; }
        .recbtn:hover { background: #FBEFEC; }
        .recbtn.on { background: #C8341E; border-color: #C8341E; }
        .rtz { min-height: 34px; padding: 0 14px; border: 1px solid #C9C4B6;
               background: #FCFBF8; border-radius: 8px; font-size: 11px;
               font-weight: 700; letter-spacing: 0.06em; box-shadow: none;
               color: #1A1916; }
        .rtz:hover { background: #EFEBE0; }
        .counter { border: 1px solid #C9C4B6; background: #FCFBF8;
                   color: #1A1916; padding: 6px 16px; border-radius: 8px;
                   font-size: 20px; font-weight: 600; letter-spacing: 0.02em; }
        .tstatus { font-size: 12px; color: #6E695E; }
        .caps { font-size: 11px; color: #6E695E; font-weight: 700;
                letter-spacing: 0.14em; }
        .smallnum { font-size: 12px; color: #6E695E; }
        .vsep { color: #D7D2C5; margin: 12px 0; }
        .metrobtn { min-height: 30px; padding: 0 12px; border: 1px solid #C9C4B6;
                    background: #FCFBF8; border-radius: 8px; font-size: 10px;
                    font-weight: 700; letter-spacing: 0.08em; box-shadow: none;
                    color: #6E695E; }
        .metrobtn:hover { background: #EFEBE0; }
        .metrobtn.on { background: #1A1916; border-color: #1A1916;
                       color: #FCFBF8; }
        .lenbtn { min-height: 30px; min-width: 58px; padding: 0 12px;
                  border: 1px solid #C9C4B6; background: #FCFBF8;
                  border-radius: 8px; font-size: 13px; font-weight: 600;
                  box-shadow: none; color: #1A1916; }
        .lenbtn:hover { background: #EFEBE0; }

        /* ---- view switcher ---- */
        .viewbar { background: #F8F7F2; border-bottom: 1px solid #C9C4B6;
                   padding: 5px 16px; }
        .viewbar * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        /* one segmented control, so the three views read as three states of
           the same thing rather than three unrelated buttons */
        .seg { border: 1px solid #C9C4B6; border-radius: 8px;
               background: #FCFBF8; }
        .segbtn { min-height: 24px; padding: 0 16px; border: none;
                  border-right: 1px solid #D7D2C5; border-radius: 8px;
                  background: transparent; box-shadow: none; font-size: 10px;
                  font-weight: 700; letter-spacing: 0.1em; color: #6E695E; }
        .segbtn:hover { background: #EFEBE0; }
        .segbtn.on { background: #1A1916; color: #FCFBF8; }
        .viewctx { font-size: 11px; color: #9A9484; padding-left: 16px; }

        /* ---- note editor ---- */
        .editbar { background: #F1EEE6; border-bottom: 1px solid #C9C4B6;
                   min-height: 40px; padding: 0 16px; }
        .editbar * { font-family: "Nimbus Sans","Helvetica",sans-serif;
                     color: #1A1916; }
        .cliplbl { font-size: 13px; font-weight: 700; color: #1A1916; }
        .chip { min-height: 24px; padding: 0 10px; border: 1px solid #C9C4B6;
                background: #FCFBF8; border-radius: 8px; font-size: 10px;
                font-weight: 700; letter-spacing: 0.06em; box-shadow: none;
                color: #1A1916; }
        .chip:hover { background: #EFEBE0; }
        /* a chip that is DOING something reverses, exactly as METRO does */
        .chip.on { background: #1A1916; color: #FCFBF8; border-color: #1A1916; }
        .editact { min-height: 24px; padding: 0 12px;
                   border: 1px solid #C9C4B6; background: #FCFBF8;
                   border-radius: 8px; font-size: 12px; box-shadow: none;
                   color: #1A1916; }
        .editact:hover { background: #EFEBE0; }
        .emptybig { font-size: 14px; color: #9A9484; }

        /* ---- mixer ---- */
        .mixer { background: #FCFBF8; }
        .strip { background: #F8F7F2; border-right: 1px solid #D7D2C5;
                 padding: 10px 8px 12px 8px; }
        .strip * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .masterstrip { background: #F1EEE6; border-left: 2px solid #C9C4B6; }
        .stripname { font-size: 12px; font-weight: 700; color: #1A1916; }

        /* ---- list card ---- */
        .cardrow { min-height: 28px; padding: 2px 12px; border: none;
                   background: transparent; box-shadow: none; font-size: 13px;
                   color: #1A1916; border-radius: 6px; }
        .cardrow:hover { background: #EFEBE0; }

        /* ---- the timeline's tool / grid / zoom bar ---- */
        .timelinebar { background: #F8F7F2;
                       border-bottom: 1px solid #D7D2C5;
                       min-height: 36px; padding: 0 16px; }
        .timelinebar * { font-family: "Nimbus Sans","Helvetica",sans-serif;
                         color: #1A1916; }
        /* the two zoom steppers: one glyph wide, so they read as a pair of
           buttons on one control rather than two separate commands */
        .zbtn { min-height: 24px; min-width: 26px; padding: 0 4px;
                border: 1px solid #C9C4B6; background: #FCFBF8;
                border-radius: 8px; font-size: 15px; box-shadow: none;
                color: #1A1916; }
        .zbtn:hover { background: #EFEBE0; }

        /* ---- the tape's own scrollbar ---- */
        .hscrollrow { background: #F1EEE6; border-top: 1px solid #D7D2C5; }

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
        /* the armed lane is marked in INK, not the accent: a 3px accent
           edge means SELECTED everywhere else in the OS (Tasks, Academics,
           Journal, Cookbook, Contacts, Packages, Music), and this row is not
           a selection - it is the lane the machine will record onto. The REC
           chip beside it carries the recording meaning in the one signage
           red. */
        .trackhead.armed { box-shadow: inset 3px 0 0 #1A1916; }
        .trackhead * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .trackname { font-size: 14px; font-weight: 700; color: #1A1916; }
        /* The instrument is typed now. Framed like an entry so it reads as
           editable, but quiet enough not to compete with the track number
           beside it. */
        .nameentry { font-size: 13px; font-weight: 700; color: #1A1916;
                     background: #FCFBF8; border: 1px solid #D7D2C5;
                     border-radius: 8px; box-shadow: none; padding: 1px 6px;
                     min-height: 22px; }
        .nameentry:focus { border-color: #B3AD9E; }
        .mbtn, .sbtn, .armbtn { min-height: 22px; min-width: 22px;
                         padding: 2px 8px; font-size: 10px;
                         font-weight: 700; letter-spacing: 0.04em;
                         border: 1px solid #C9C4B6; background: #FCFBF8;
                         color: #6E695E; border-radius: 8px; box-shadow: none; }
        .mbtn:hover, .sbtn:hover, .armbtn:hover { background: #EFEBE0; }
        .mbtn.on { background: #1A1916; border-color: #1A1916; color: #FCFBF8; }
        /* Mute and Solo are ENGAGED CONTROLS, so both are ink chips (as the
           metronome is, and as Music paints shuffle/repeat). Solo used to be
           the signage red, which put a second meaning on the one colour this
           screen reserves for recording. */
        .sbtn.on { background: #1A1916; border-color: #1A1916; color: #FCFBF8; }
        /* THE ONE SIGNAGE RED on this window means RECORD: the transport's
           record button, and the REC chip of every track that will be
           recorded onto. Nothing else may take it. */
        .armbtn { color: #6E695E; }
        .armbtn.on { background: #C8341E; border-color: #C8341E;
                     color: #FCFBF8; }
        .clrbtn { min-height: 22px; min-width: 24px; padding: 1px 4px;
                  border: 1px solid #C9C4B6; background: #FCFBF8;
                  border-radius: 8px; box-shadow: none; }
        .clrbtn:hover { background: #FBEFEC; }
        .clrbtn:disabled { opacity: 0.35; }
        /* the click-to-change caret, quieter than the value it sits beside */
        .caret { font-size: 10px; color: #9A9484; }
        .minicap { font-size: 10px; color: #9A9484; font-weight: 700;
                   letter-spacing: 0.12em; }

        /* ---- sliders ---- */
        scale { padding: 0; margin: 0; }
        scale trough { min-height: 4px; background: #D7D2C5;
                       border: none; border-radius: 100px; }
        scale highlight { background: #1A1916; border-radius: 100px; }
        scale slider { min-width: 13px; min-height: 13px; margin: -6px;
                       background: #1A1916; border: none; border-radius: 50%;
                       box-shadow: none; }

        /* ---- status bar ---- */
        /* .statusbar is Papertone's - see the theme. */

        /* ---- confirm card (destructive-action prompt) ---- */
        .seqscrim { background: rgba(26,25,22,0.28); }
        /* min-width sets the card's measure: a wrapping label's natural width
           is computed from the font's AVERAGE character, which for this face
           is far narrower than the real text, so the body was breaking into a
           ragged four-line column half the width of its own title. */
        .seqprompt { background: #F8F7F2; border: 1px solid #C9C4B6;
                     padding: 22px 26px; min-width: 330px; }
        .seqprompt * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .seqprompttitle { font-size: 17px; font-weight: 700; color: #1A1916; }
        .seqpromptmsg { font-size: 13px; color: #6E695E; }
        .seqpromptcancel { min-height: 30px; padding: 0 16px;
                           border: 1px solid #C9C4B6; background: #FCFBF8;
                           border-radius: 8px; font-size: 13px; color: #1A1916;
                           box-shadow: none; }
        .seqpromptcancel:hover { background: #EFEBE0; }
        .seqpromptok { min-height: 30px; padding: 0 16px;
                       border: 1px solid #C8341E; background: #C8341E;
                       border-radius: 8px; font-size: 13px; font-weight: 700;
                       color: #FCFBF8; box-shadow: none; }
        .seqpromptok:hover { background: #B12D19; }

        /* The system theme sets `* { color: ink }`, which matches a button's
           LABEL node directly, so a colour set on the button itself never
           reaches its text. Every reversed (dark/red) control therefore has to
           name the label too, or it renders ink-on-ink: an engaged METRO and a
           muted track's M were solid unreadable slabs, and the confirm card's
           primary button lost its white text. */
        .metrobtn.on label, .mbtn.on label, .sbtn.on label,
        .armbtn.on label, .seqpromptok label, .segbtn.on label,
        .chip.on label { color: #FCFBF8; }
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


def _clip_json(c):
    """One clip as the smallest dict that still says everything about it."""
    out = {"s": round(c["s"], 4), "e": round(c["e"], 4)}
    if c.get("wav"):
        out["wav"] = c["wav"]
        if c.get("off"):
            out["off"] = round(c["off"], 4)
        if abs(c.get("gain", 1.0) - 1.0) > 1e-6:
            out["gain"] = round(c["gain"], 4)
        if abs(c.get("fin", 0.005) - 0.005) > 1e-6:
            out["fin"] = round(c["fin"], 4)
        if abs(c.get("fout", 0.005) - 0.005) > 1e-6:
            out["fout"] = round(c["fout"], 4)
    return out


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


def _fmt_fade(v):
    """A fade length as something readable on a chip."""
    if v <= 0.0009:
        return _t("OFF")
    if v < 1.0:
        return "%d MS" % int(round(v * 1000))
    return "%.2g S" % v


def _fmt_len(v):
    v = int(round(v))
    return "%02d:%02d" % (v // 60, v % 60)


def _fmt_dur(v):
    """A duration the way it is spoken about at that size: a clip is "2.4 s"
    and a song is "03:12". mm:ss for four seconds reads as almost nothing."""
    v = max(0.0, float(v))
    if v < 10:
        return "%.2f s" % v
    if v < 60:
        return "%.1f s" % v
    return _fmt_len(v)


if __name__ == "__main__":
    nbapp.run(Sequencer)
