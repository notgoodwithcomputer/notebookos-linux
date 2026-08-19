#!/usr/bin/env python3
"""
Disc Burner — put music on a CD, or video on a DVD that plays in a DVD player.

Two jobs, and they are genuinely different things rather than two buttons over
one mechanism:

  * **Audio CD.** The chosen songs are decoded to Red Book PCM — 44100 Hz,
    16-bit, stereo, no exceptions, because that is the only thing a CD player
    can read — and written disc-at-once so the tracks run into each other with
    no two-second hole between them. What comes out plays in a car.

  * **Video DVD.** The chosen videos are re-encoded to DVD-compliant MPEG-2,
    given a menu, and authored into the VIDEO_TS structure a DVD player
    actually looks for. A disc with the files merely copied onto it is a data
    disc: a computer will open it and a DVD player will reject it. The
    difference is the IFO navigation tables, and building those is most of the
    work here.

THE MENU is deliberately the plainest thing that can still be navigated: white
text on black, the disc name, then a numbered list of titles. That is a choice,
not an unfinished state. It is drawn with cairo at the frame size the standard
demands (720x480 for NTSC, 720x576 for PAL) — the same drawing stack the rest of
the OS uses — so the type is the OS's type, and there is no theme, gradient or
image to go wrong on a television.

How a DVD menu actually works, since nothing about it is guessable: the menu is
an MPEG still with a *subpicture* stream layered over it, and the subpicture is
what lights up under the selected row. `spumux` muxes that layer in and needs
the button rectangles in frame coordinates; `dvdauthor` then writes the IFO
tables that map each button to a title. Both read the same `menu_layout()`, so
the rectangle a person's remote highlights is by construction the rectangle the
text was drawn in — the two cannot drift apart.

TRAPS, each of which cost a run to find:

  * **From a VMGM menu you may only `jump title N`.** `jump titleset 1 title 1`
    is rejected outright ("That form of jumping is not allowed") — dvdauthor
    exits 1 and leaves a half-built VIDEO_TS behind. Titles are numbered across
    the whole disc from the menu's point of view.
  * **Silence still has to be stereo.** A menu built over a mono or absent audio
    track is out of spec; players show a black screen. The menu gets a real
    silent 48 kHz stereo track, and every title is forced to `-ac 2`.
  * **A DVD needs UDF, and not every tool writes it.** genisoimage does
    (`-udf -dvd-video`); xorriso does not, which is why cdrkit is the media
    backend here.
  * **Building a DVD needs about three times the finished disc in scratch
    space** — the transcodes, then VIDEO_TS, then the ISO. Running out halfway
    through wastes twenty minutes of encoding, so the room is checked before
    the first frame is encoded, not when the write fails.

Everything slow runs on an nbjobs worker: encoding an hour of video takes an
hour, and a frozen window is indistinguishable from a crash. One job key means
a second burn cannot start on top of a running one, cancellation is a token the
worker checkpoints between files, and no callback reaches a widget after the
window has gone.
"""
import os
import select
import re
import shutil
import subprocess
import tempfile

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import GLib, Gtk, Pango, PangoCairo      # noqa: E402

import cairo                                                # noqa: E402

import nbapp                                                # noqa: E402
import nbjobs                                               # noqa: E402
import nbpicker                                             # noqa: E402
import nbi18n
from nbi18n import _t                                       # noqa: E402

HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
MUSIC_DIR = os.path.join(HOME, "Music")
VIDEOS_DIR = os.path.join(HOME, "Videos")

AUDIO_EXTS = (".wav", ".flac", ".mp3", ".ogg", ".oga", ".opus", ".m4a",
              ".aac", ".alac", ".aiff", ".aif", ".wma")
VIDEO_EXTS = (".mp4", ".mkv", ".avi", ".mov", ".webm", ".mpg", ".mpeg",
              ".m4v", ".wmv", ".flv", ".ogv", ".3gp")

# Red Book counts TIME, not bytes: one second of CD audio is 75 sectors of
# 2352 bytes whatever the source file was. A nominal 80-minute disc holds
# 79:57 of programme once the lead-in and lead-out are taken, and burners
# differ in the last few sectors, so that is the number offered rather than a
# round 80:00 that fails on the final track.
CD_MAX_SECONDS = 79 * 60 + 57
CD_BYTES_PER_SECOND = 44100 * 2 * 2      # 44.1 kHz, stereo, 16-bit

# A single-layer DVD-R holds 4,700,372,992 bytes. It is sold as "4.7 GB" and
# reported by every file manager as 4.38 GiB; both are the same disc. Leave a
# margin for the ISO's own structures rather than filling to the last sector.
DVD_BYTES = 4700372992
DVD_USABLE = int(DVD_BYTES * 0.97)

# (name, width, height, fps, ffmpeg -target)
NTSC = ("ntsc", 720, 480, 30000.0 / 1001.0, "ntsc-dvd")
PAL = ("pal", 720, 576, 25.0, "pal-dvd")

# Menus are 4:3 so they fill an old television and letterbox politely on a wide
# one; the titles keep their own shape.
MENU_SECONDS = 4
MENU_AUDIO_RATE = 48000

# DVD-Video allows 9.8 Mbit/s across video+audio. Sit well under it: a player
# that stutters on a legal-but-marginal disc is indistinguishable from a bad
# burn, and nobody watching can tell you the muxrate was too high.
DVD_PEAK_KBIT = 8000
DVD_MIN_KBIT = 1500
DVD_AUDIO_KBIT = 192

MAX_TITLES = 9          # the menu is one screen and a remote has ten digits

# How tall the contents list asks to be, per mode. The list is the one part of
# the column that can give room back, and in Video DVD mode it has to: the DISC
# NAME field and its hint cost about 100px, which on the smallest supported
# panel (1024x740) pushed the status bar clean off the bottom edge — taking
# every phase, refusal and result sentence in that whole half of the app with
# it. A shorter list is a visible cost; an invisible status line is not.
LIST_MIN_AUDIO = 190
LIST_MIN_VIDEO = 150


# ---- reading the machine ----------------------------------------------------
def _set_user_text(label, text):
    """A track name in the burn queue, exactly as the file is called.

    item["name"] is the filename with its extension STRIPPED, so ~/Music/
    Drums.wav arrives here as the bare word "Drums" — a catalog key. The disc
    menu is rendered from the same list, so the screen said "Batterie" and the
    burned DVD said "Drums"."""
    nbi18n.set_verbatim(label, str(text or ""))


def _read(path, default=""):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return default


def _listdir(path):
    """Never raises. A drive list that collapses to nothing because /sys was
    unreadable looks exactly like a machine with no drive in it."""
    try:
        return sorted(os.listdir(path))
    except OSError:
        return []


def optical_drives():
    """Every optical drive the kernel knows about, newest name order.

    /sys/block/sr* is the whole story: the kernel only creates those for
    devices that answered as a CD/DVD unit, so there is no guessing to do and
    no chance of offering a hard disk by mistake — which is the entire reason
    this app needs no equivalent of USB Writer's system-disk guards.
    """
    out = []
    for name in _listdir("/sys/block"):
        if not name.startswith("sr"):
            continue
        base = os.path.join("/sys/block", name)
        vendor = _read(os.path.join(base, "device/vendor"))
        model = _read(os.path.join(base, "device/model"))
        label = " ".join(p for p in (vendor, model) if p) or name
        out.append({"name": name, "node": "/dev/" + name, "label": label})
    return out


_MEDIA_RE = re.compile(r"^\s*Mounted Media:\s*\S+,\s*(.+?)\s*$", re.M)
_BLANK_RE = re.compile(r"^\s*Disc status:\s*(\S+)", re.M)
_CAPACITY_RE = re.compile(r"^\s*Free Blocks:\s*(\d+)\*2048", re.M)
_TRACK_RE = re.compile(r"^\s*Track Size:\s*(\d+)\*2048", re.M)


def disc_info(node, run=None):
    """What is in the drive, as far as the drive will say.

    Returns a dict with `media` (a human string), `blank` (True/False/None) and
    `bytes` (capacity or None). Every field is independently optional: a drive
    that answers half the questions is normal, and the UI has to be able to say
    "a disc" without knowing which kind. None everywhere means "could not ask",
    which is NOT the same as "no disc" and must not be rendered as one.
    """
    runner = run or _run
    info = {"media": None, "blank": None, "bytes": None, "present": None}
    code, out = runner(["dvd+rw-mediainfo", node], timeout=20)
    if code is None:
        return info
    if "no media" in out.lower() or "no disc" in out.lower():
        info["present"] = False
        return info
    m = _MEDIA_RE.search(out)
    if m:
        info["present"] = True
        info["media"] = m.group(1)
    m = _BLANK_RE.search(out)
    if m:
        # Some drives omit the "Mounted Media" line but still report a disc
        # status. That is positive presence evidence, not an unknown probe.
        info["present"] = True
        info["blank"] = m.group(1).lower().startswith("blank")
    m = _CAPACITY_RE.search(out) or _TRACK_RE.search(out)
    if m:
        try:
            info["bytes"] = int(m.group(1)) * 2048
            info["present"] = True
        except ValueError:
            pass
    return info


def compatible_media(mode, info):
    """A known blank medium the selected authoring path can actually use."""
    if not isinstance(info, dict) or info.get("present") is not True \
            or info.get("blank") is not True:
        return False
    media = str(info.get("media") or "").upper()
    return ("CD" in media) if mode == "audio" else ("DVD" in media)


def _run(cmd, timeout=30):
    """(exit code, combined output), or (None, "") if the tool is missing.

    A missing tool and a tool that ran and failed are different answers and the
    caller has to be able to tell them apart — the burn tools are a package
    that a stripped image could be built without.
    """
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None, ""
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def tools_present():
    """Which of the burn tools this image actually shipped."""
    return {name: shutil.which(name) is not None
            for name in ("ffmpeg", "ffprobe", "wodim", "growisofs",
                         "genisoimage", "dvdauthor", "spumux")}


def media_duration(path, run=None):
    """Seconds of programme in a media file, or None if it cannot be read."""
    runner = run or _run
    code, out = runner(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of", "default=nw=1:nk=1", path],
                       timeout=60)
    if not code == 0:
        return None
    try:
        return max(0.0, float(out.strip().splitlines()[0]))
    except (ValueError, IndexError):
        return None


# ---- capacity ---------------------------------------------------------------
def fmt_time(seconds):
    """m:ss, which is how a track length is written on a sleeve."""
    seconds = int(round(max(0, seconds)))
    return "%d:%02d" % (seconds // 60, seconds % 60)


def fmt_size(n):
    if n is None:
        return "-"
    for unit, step in (("GB", 1000 ** 3), ("MB", 1000 ** 2), ("kB", 1000)):
        if n >= step:
            return "%.1f %s" % (n / float(step), unit)
    return "%d bytes" % n


def cd_fits(durations):
    """(total seconds, whether it fits an 80-minute CD)."""
    total = sum(d for d in durations if d)
    return total, total <= CD_MAX_SECONDS


def dvd_video_bitrate(total_seconds, usable_bytes=DVD_USABLE):
    """Video bitrate in kbit/s that lands the whole programme on one disc.

    Solved from the disc rather than picked: an hour and a two-hour film cannot
    share a constant, and a fixed "DVD quality" number either wastes half the
    disc or overruns it at the end of a long film — and overrunning is only
    discovered after the encode, which is the expensive way to find out.
    """
    if not total_seconds or total_seconds <= 0:
        return DVD_PEAK_KBIT
    budget_kbit = (usable_bytes * 8.0 / 1000.0) / total_seconds
    video = budget_kbit - DVD_AUDIO_KBIT
    return int(max(DVD_MIN_KBIT, min(DVD_PEAK_KBIT, video)))


def dvd_fits(total_seconds, usable_bytes=DVD_USABLE):
    """Whether the programme can be held at a watchable bitrate.

    Below DVD_MIN_KBIT MPEG-2 stops being worth burning, so "it fits" means
    "it fits without going under that", not "the arithmetic closes".
    """
    if not total_seconds:
        return True
    return dvd_video_bitrate(total_seconds, usable_bytes) > DVD_MIN_KBIT


def scratch_needed(total_seconds):
    """Bytes of working room a DVD build wants before it starts.

    The transcodes, then a copy of them inside VIDEO_TS, then the ISO — three
    passes over the same programme. Checked up front because discovering it at
    the ISO step throws away the whole encode.
    """
    payload = min(DVD_USABLE, int((dvd_video_bitrate(total_seconds)
                                   + DVD_AUDIO_KBIT) * 1000 / 8.0
                                  * max(1, total_seconds)))
    return int(payload * 3)


def free_space(path):
    try:
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize
    except OSError:
        return None


# ---- the menu ---------------------------------------------------------------
def menu_layout(titles, standard=NTSC):
    """Geometry for the menu screen, in frame pixels.

    ONE source of truth for three consumers that must agree exactly: the text
    is drawn here, the highlight blocks are drawn here, and the button
    rectangles handed to spumux come from here. Computed rather than drawn so a
    test can check the arithmetic without rendering anything.

    Everything sits inside a title-safe inset. Televisions overscan — a tenth
    of the frame can be behind the bezel — and text at the true edge of a
    720x480 frame is simply not on the screen in the living room.
    """
    _name, w, h, _fps, _target = standard
    inset_x = int(w * 0.10)
    inset_y = int(h * 0.10)
    heading_y = inset_y + 40
    row_h = 44
    first_row = heading_y + 46
    rows = []
    for i, text in enumerate(titles[:MAX_TITLES]):
        top = first_row + i * row_h
        rows.append({
            "index": i,
            "number": i + 1,
            "text": text,
            "top": top,
            "baseline": top + 30,
            "marker": (inset_x, top + 12, 16, 16),
            # The button is the whole row, not the few pixels of the marker: a
            # remote's up/down lands on a band, and a person aiming at the
            # words expects the words to be the target.
            "button": (inset_x - 12, top, w - inset_x + 12, top + row_h - 6),
        })
    return {"width": w, "height": h, "inset_x": inset_x, "inset_y": inset_y,
            "heading_y": heading_y, "row_h": row_h, "text_x": inset_x + 34,
            "rows": rows}


def _menu_text(cr, x, baseline, text, size, bold=False):
    """Draw one line of menu text with its BASELINE at `baseline`.

    Through Pango, not cairo's `select_font_face`/`show_text`. The toy API
    takes one family and stops there: it does no fallback and no shaping, so a
    disc named in Japanese, Hindi or Yiddish comes out as empty boxes — and the
    disc name is a field a person types in an OS that speaks seventeen
    languages. Pango picks a face per run and shapes it.

    Pango positions a layout by its TOP edge, while the layout here is written
    in baselines (a subpicture button has to line up with the text a person
    reads), so the baseline is converted back to a top with the layout's own
    metric rather than a guessed line-height.
    """
    layout = PangoCairo.create_layout(cr)
    desc = Pango.FontDescription()
    desc.set_family("Nimbus Sans,DejaVu Sans,sans-serif")
    desc.set_absolute_size(size * Pango.SCALE)
    if bold:
        desc.set_weight(Pango.Weight.BOLD)
    layout.set_font_description(desc)
    layout.set_text(text, -1)
    cr.move_to(x, baseline - layout.get_baseline() / Pango.SCALE)
    PangoCairo.show_layout(cr, layout)


def render_menu_png(path, disc_title, titles, standard=NTSC):
    """The menu background: black, with white text. Nothing else.

    Written with cairo at the exact frame size, so what the encoder receives is
    already the right shape and no scaler gets to soften the type.
    """
    lay = menu_layout(titles, standard)
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, lay["width"], lay["height"])
    cr = cairo.Context(surf)
    cr.set_source_rgb(0, 0, 0)
    cr.paint()
    cr.set_source_rgb(1, 1, 1)
    if disc_title:
        _menu_text(cr, lay["inset_x"], lay["heading_y"], disc_title, 34,
                   bold=True)
    for row in lay["rows"]:
        _menu_text(cr, lay["text_x"], row["baseline"],
                   "%d.  %s" % (row["number"], row["text"]), 26)
    surf.flush()
    surf.write_to_png(path)
    return lay


def render_highlight_png(path, layout):
    """The subpicture layer: transparent everywhere except a block beside each
    row, which is what appears under the selection.

    A subpicture is a 2-bit image — four colours, one of them transparency — so
    this stays to one solid colour on nothing. Anti-aliased edges or a gradient
    would be quantised to garbage.
    """
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                              layout["width"], layout["height"])
    cr = cairo.Context(surf)
    cr.set_operator(cairo.OPERATOR_SOURCE)
    cr.set_source_rgba(0, 0, 0, 0)
    cr.paint()
    cr.set_antialias(cairo.ANTIALIAS_NONE)
    cr.set_source_rgba(1, 1, 1, 1)
    for row in layout["rows"]:
        x, y, w, h = row["marker"]
        cr.rectangle(x, y, w, h)
        cr.fill()
    surf.flush()
    surf.write_to_png(path)


def spumux_xml(layout):
    """The subpicture description, with one button per row."""
    lines = ['<subpictures>', '  <stream>',
             '    <spu force="yes" start="00:00:00.00"',
             '         highlight="highlight.png" select="highlight.png">']
    for row in layout["rows"]:
        x0, y0, x1, y1 = row["button"]
        lines.append('      <button name="t%d" x0="%d" y0="%d" x1="%d" '
                     'y1="%d"/>' % (row["number"], x0, y0, x1, y1))
    lines += ['    </spu>', '  </stream>', '</subpictures>', '']
    return "\n".join(lines)


def dvdauthor_xml(dest, menu_vob, title_vobs, standard=NTSC):
    """The disc's navigation, as dvdauthor wants it.

    `jump title N` — never `jump titleset 1 title N`. From the VMGM domain the
    latter is rejected outright and dvdauthor exits after having already
    written a partial VIDEO_TS, which looks like a successful build until a
    player refuses the disc.
    """
    fmt = standard[0]
    out = ['<dvdauthor dest="%s">' % dest,
           '  <vmgm>',
           '    <menus>',
           '      <video format="%s" aspect="4:3"/>' % fmt,
           '      <pgc entry="title">',
           '        <vob file="%s" pause="inf"/>' % menu_vob]
    for i in range(len(title_vobs)):
        out.append('        <button name="t%d"> jump title %d; </button>'
                   % (i + 1, i + 1))
    out += ['      </pgc>', '    </menus>', '  </vmgm>',
            '  <titleset>', '    <titles>',
            '      <video format="%s" aspect="16:9"/>' % fmt]
    for vob in title_vobs:
        # Returning to the menu at the end of each title is what makes the disc
        # feel finished; without the post the player simply stops.
        out.append('      <pgc><vob file="%s"/>'
                   '<post> call vmgm menu 1; </post></pgc>' % vob)
    out += ['    </titles>', '  </titleset>', '</dvdauthor>', '']
    return "\n".join(out)


# ---- the commands -----------------------------------------------------------
def decode_track_cmd(src, dst):
    """Any audio file -> Red Book PCM. -vn drops cover art, which is a video
    stream as far as ffmpeg is concerned and makes the WAV unreadable."""
    return ["ffmpeg", "-nostdin", "-y", "-i", src, "-vn",
            "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le",
            "-f", "wav", dst]


def audio_burn_cmd(node, tracks, speed=None):
    """-dao writes the whole disc in one pass, which is what removes the
    two-second gap track-at-once would insert between every song. -pad rounds
    each track up to a whole sector; without it a track whose length is not a
    multiple of 2352 bytes is refused."""
    cmd = ["wodim", "-v", "dev=" + node, "-audio", "-pad", "-dao"]
    if speed:
        cmd.append("speed=%d" % speed)
    return cmd + list(tracks)


def transcode_title_cmd(src, dst, standard=NTSC, kbit=None):
    """One video -> a DVD-legal MPEG-2 programme stream.

    `-target ntsc-dvd` is doing a great deal of work: frame size, frame rate,
    GOP, muxrate, packet size and audio codec all have legal values it knows
    and this app would otherwise have to carry. `-ac 2` is added because the
    target does not force it and a mono source stays mono, which is out of
    spec.
    """
    cmd = ["ffmpeg", "-nostdin", "-y", "-i", src,
           "-target", standard[4], "-aspect", "16:9", "-ac", "2"]
    if kbit:
        cmd += ["-b:v", "%dk" % kbit, "-maxrate", "%dk" % min(9000, kbit + 1500),
                "-bufsize", "1835k"]
    return cmd + ["-progress", "pipe:1", "-nostats", dst]


def menu_video_cmd(png, dst, standard=NTSC):
    """A still image plus real silence, for MENU_SECONDS.

    The silent track is not decoration: a menu with no audio stream is out of
    spec and players show black. anullsrc gives it a genuine stereo 48 kHz
    stream to carry.
    """
    return ["ffmpeg", "-nostdin", "-y", "-loop", "1", "-i", png,
            "-f", "lavfi", "-i",
            "anullsrc=channel_layout=stereo:sample_rate=%d" % MENU_AUDIO_RATE,
            "-t", str(MENU_SECONDS), "-target", standard[4],
            "-aspect", "4:3", "-ac", "2", dst]


def iso_cmd(root, iso, label):
    """-dvd-video lays the files out the way a player expects to find them and
    -udf adds the filesystem it reads. ISO9660 alone is a coin toss on a set-top
    box."""
    return ["genisoimage", "-dvd-video", "-udf", "-V", label[:32],
            "-o", iso, root]


def dvd_burn_cmd(node, iso):
    """-dvd-compat closes the disc so it is readable in a player rather than
    only in the drive that wrote it."""
    return ["growisofs", "-dvd-compat", "-Z", "%s=%s" % (node, iso)]


def disc_label(title):
    """A volume label a DVD will accept: upper case, A-Z 0-9 and _, 32 max."""
    out = "".join(c if c.isalnum() else "_" for c in (title or "").upper())
    out = re.sub(r"_+", "_", out).strip("_")
    return (out or "DISC")[:32]


# ---- exceptions the UI reports by CLASS, never by matching words ------------
class BurnError(Exception):
    """Base for everything this app reports. Matched on the class: the message
    is built with _t() and a translated string cannot be pattern-matched."""


class NoDisc(BurnError):
    pass


class DiscTooSmall(BurnError):
    pass


class NoRoom(BurnError):
    pass


class ToolMissing(BurnError):
    pass


class StepFailed(BurnError):
    def __init__(self, message, detail=""):
        super().__init__(message)
        self.detail = detail


# ---- the pipelines ----------------------------------------------------------
def _end_group(proc):
    """End a burn step and everything it started.

    Signals the process GROUP: the tools here are not one process each, and a
    terminate() aimed at the one we launched leaves its children holding the
    pipe we are still reading — which is how a cancelled burn hangs instead of
    stopping. SIGKILL follows if the polite signal is ignored, so Stop always
    means stopped."""
    import signal as _signal
    try:
        group = os.getpgid(proc.pid)
    except OSError:
        group = None
    for sig, wait in ((_signal.SIGTERM, 3), (_signal.SIGKILL, 2)):
        try:
            if group is not None:
                os.killpg(group, sig)
            else:
                proc.send_signal(sig)
        except (OSError, ValueError):
            pass
        try:
            proc.wait(timeout=wait)
            return
        except Exception:                                         # noqa: BLE001
            continue


def _step(job, cmd, phase, fraction=None, span=0.0, progress_seconds=None,
          source=None):
    """Run one tool, checkpointing for cancellation, reporting as it goes.

    `fraction` is where this step starts on the overall bar and `span` is how
    much of the bar it covers, so a long encode can move the bar WITHIN its own
    share instead of sitting still and then jumping. ffmpeg's `-progress`
    output gives the position; anything else just holds at `fraction`.
    """
    job.checkpoint()
    if shutil.which(cmd[0]) is None:
        raise ToolMissing(_t("This disc needs %s, which is not installed.")
                          % cmd[0])
    if fraction is not None:
        job.progress(fraction, phase)
    try:
        # ITS OWN PROCESS GROUP, so Stop can reach the whole burn. growisofs
        # execs mkisofs, dvdauthor runs its own muxers, ffmpeg spawns for
        # filters — terminate() reaches only the process we launched. The
        # survivor keeps the write end of the pipe below open, so the read loop
        # never sees EOF and the burn thread hangs on a cancel that appeared to
        # work, with the drive still being written. Same defect that wedged the
        # GBA toolchain build and Video's export cancel.
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                bufsize=1, start_new_session=True)
    except OSError as exc:
        raise StepFailed(_t("%s could not be started.") % cmd[0],
                         str(exc)) from exc
    tail = []
    try:
        # POLLED, NOT BLOCKED. This used to read the tool's output with a plain
        # `for line in proc.stdout`, which parks until the next line arrives —
        # and the cancel check lived INSIDE that loop. A burn is mostly silence:
        # wodim says nothing while it writes a track, growisofs nothing while it
        # closes a session. So Stop did nothing at all until the tool happened
        # to speak again, which on a long track is minutes. Waking on a timer
        # makes the button mean what it says.
        while True:
            ready = select.select([proc.stdout], [], [], 0.4)[0]
            if not ready:
                # A PROPERTY on nbjobs.Job (it delegates to the cancel token's
                # own property), so `job.cancelled()` calls a bool and raises
                # TypeError inside the worker — which is what every burn did,
                # before the first track was even decoded.
                if job.cancelled:
                    _end_group(proc)
                    job.checkpoint()
                    break
                if proc.poll() is not None:
                    break              # finished during a quiet stretch
                continue
            line = proc.stdout.readline()
            if not line:
                break                  # end of output: the tool is done
            tail.append(line)
            del tail[:-40]
            if job.cancelled:
                _end_group(proc)
                job.checkpoint()
                break
            if progress_seconds and span and fraction is not None:
                m = re.match(r"out_time_ms=(\d+)", line.strip())
                if m:
                    # ffmpeg reports microseconds under a key that says ms.
                    done = int(m.group(1)) / 1000000.0
                    share = min(1.0, done / max(0.001, progress_seconds))
                    job.progress(fraction + share * span, phase)
    finally:
        proc.stdout.close()
        code = proc.wait()
    job.checkpoint()
    if code != 0:
        if source:
            # This step was working on ONE of the person's files, so its
            # failure is about that file. "ffmpeg could not finish." names our
            # own plumbing instead of the thing they can do something about.
            raise StepFailed(_t("“%s” could not be read. Remove it and try "
                                "again.") % os.path.basename(source),
                             "".join(tail)[-2000:])
        raise StepFailed(_t("%s could not finish.") % cmd[0],
                         "".join(tail)[-2000:])
    return "".join(tail)


def build_audio_cd(job, node, files, workdir, speed=None):
    """Decode every song, then write the disc in one pass."""
    tracks = []
    total = max(1, len(files))
    for i, src in enumerate(files):
        job.checkpoint()
        dst = os.path.join(workdir, "track%02d.wav" % (i + 1))
        _step(job, decode_track_cmd(src, dst),
              _t("Preparing “%s”…") % os.path.basename(src),
              fraction=0.05 + 0.45 * (i / float(total)), source=src)
        tracks.append(dst)
    job.progress(0.5, _t("Writing the disc…"))
    _step(job, audio_burn_cmd(node, tracks, speed),
          _t("Writing the disc…"), fraction=0.5)
    job.progress(1.0, _t("Finishing…"))
    return True


def build_video_dvd(job, node, files, titles, disc_title, workdir,
                    standard=NTSC):
    """Transcode, draw the menu, author VIDEO_TS, master an ISO, burn it."""
    durations = [media_duration(f) or 0.0 for f in files]
    total_seconds = sum(durations)
    kbit = dvd_video_bitrate(total_seconds)
    dvd_root = os.path.join(workdir, "dvd")
    os.makedirs(dvd_root, exist_ok=True)

    vobs = []
    for i, src in enumerate(files):
        job.checkpoint()
        dst = os.path.join(workdir, "title%d.mpg" % (i + 1))
        share = 0.60 / float(max(1, len(files)))
        _step(job, transcode_title_cmd(src, dst, standard, kbit),
              _t("Converting “%s”…") % os.path.basename(src),
              fraction=0.02 + share * i, span=share,
              progress_seconds=durations[i] or None, source=src)
        vobs.append(dst)

    job.checkpoint()
    job.progress(0.64, _t("Drawing the menu…"))
    png = os.path.join(workdir, "menu.png")
    hl = os.path.join(workdir, "highlight.png")
    lay = render_menu_png(png, disc_title, titles, standard)
    render_highlight_png(hl, lay)
    menu_mpg = os.path.join(workdir, "menu.mpg")
    _step(job, menu_video_cmd(png, menu_mpg, standard),
          _t("Drawing the menu…"), fraction=0.66)

    # spumux reads its images relative to its own working directory, and takes
    # the video on stdin. Both are why this one step is not a plain _step().
    job.checkpoint()
    job.progress(0.70, _t("Adding the menu buttons…"))
    with open(os.path.join(workdir, "spumux.xml"), "w",
              encoding="utf-8") as fh:
        fh.write(spumux_xml(lay))
    menu_sub = os.path.join(workdir, "menu_sub.mpg")
    if shutil.which("spumux") is None:
        raise ToolMissing(_t("This disc needs %s, which is not installed.")
                          % "spumux")
    try:
        with open(menu_mpg, "rb") as src, open(menu_sub, "wb") as dst:
            p = subprocess.run(["spumux", "spumux.xml"], stdin=src, stdout=dst,
                               stderr=subprocess.PIPE, cwd=workdir,
                               timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        raise StepFailed(_t("%s could not finish.") % "spumux",
                         str(exc)) from exc
    if p.returncode != 0 or not os.path.getsize(menu_sub):
        raise StepFailed(_t("%s could not finish.") % "spumux",
                         (p.stderr or b"").decode("utf-8", "replace")[-2000:])

    job.checkpoint()
    job.progress(0.74, _t("Building the disc…"))
    with open(os.path.join(workdir, "dvdauthor.xml"), "w",
              encoding="utf-8") as fh:
        fh.write(dvdauthor_xml(dvd_root, menu_sub, vobs, standard))
    _step(job, ["dvdauthor", "-x", os.path.join(workdir, "dvdauthor.xml")],
          _t("Building the disc…"), fraction=0.76)

    job.checkpoint()
    job.progress(0.86, _t("Preparing the disc image…"))
    iso = os.path.join(workdir, "disc.iso")
    _step(job, iso_cmd(dvd_root, iso, disc_label(disc_title)),
          _t("Preparing the disc image…"), fraction=0.88)

    job.checkpoint()
    job.progress(0.92, _t("Writing the disc…"))
    _step(job, dvd_burn_cmd(node, iso), _t("Writing the disc…"),
          fraction=0.92)
    job.progress(1.0, _t("Finishing…"))
    return True


# ---- the window -------------------------------------------------------------
def set_reason(btn, text):
    """Set a control's state FROM the reason it is in that state.

    Sensitivity is derived from the string rather than computed beside it,
    so a button that will decline the click cannot exist without saying
    what would make it work — the defect tools/disabled_reason_check.py
    exists to stop. Passing "" is the only way to enable a control, and it
    clears the stale reason in the same call.
    """
    btn.set_sensitive(not text)
    # Write only a CHANGED reason. set_tooltip_text triggers a tooltip query
    # against the display on every call, and _refresh restates all six of
    # these controls on every keystroke in the disc name and every row
    # selection, almost always with the sentence they already carry.
    want = text or None
    if btn.get_tooltip_text() != want:
        btn.set_tooltip_text(want)


class DiscBurner(nbapp.AppWindow):
    app_name = "Disc Burner"
    menus = ("File",)

    AUDIO = "audio"
    VIDEO = "video"

    def __init__(self):
        super().__init__()
        self.mode = self.AUDIO
        # ONE LIST PER MODE. The mode pair is a segmented control sitting right
        # above the list, so a stray click used to throw away a compilation
        # that took minutes to assemble, with no warning and nowhere to get it
        # back from. Switching parks the list; switching back restores it.
        self._lists = {self.AUDIO: [], self.VIDEO: []}
        self.items = self._lists[self.mode]   # [{path, name, seconds}]
        self.drives = []
        self.drive = None
        self.busy = False
        self._add_pending = False
        self._closing_after_stop = False
        self.standard = NTSC
        self._workdir = None
        self._jobs = nbjobs.JobOwner(name="burner")
        self.connect("delete-event", self._on_delete)
        self.connect("destroy", lambda *_: self._shutdown())
        self._install_css()
        self._build()
        self._rescan()

    def _shutdown(self):
        self._jobs.close()
        self._clean_workdir()

    def _clean_workdir(self):
        if self._workdir and os.path.isdir(self._workdir):
            shutil.rmtree(self._workdir, ignore_errors=True)
        self._workdir = None

    # -- ui -------------------------------------------------------------------
    def _install_css(self):
        # ASCII only: one non-ASCII byte silently kills the whole stylesheet.
        css = b"""
        .db-main { background: #FCFBF8; }
        .db-main * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .db-title { font-size: 24px; font-weight: 700; color: #1A1916; }
        .db-lede { font-size: 14px; color: #6E695E; }
        .db-step { font-size: 11px; letter-spacing: 0.14em; font-weight: 700;
                   color: #6E695E; }
        .db-field { background: #F4F2EC; border: 1px solid #D7D2C5; }
        .db-value { font-size: 15px; color: #1A1916; }
        .db-hint { font-size: 12px; color: #6E695E; }
        .db-warn { font-size: 13px; color: #C8341E; }
        .db-btn { padding: 6px 14px; background: #FCFBF8;
                  border: 1px solid #C9C4B6; border-radius: 8px;
                  box-shadow: none; font-size: 14px; color: #1A1916; }
        .db-btn:hover { background: #F1EEE6; }
        /* The contents rows ARE .db-btn toggles, and this flat background beat
           Papertone's own button:checked (a provider priority wins whatever the
           selectors say), so the chosen row was pixel-identical to the others
           while Move up/Move down acted on it. */
        .db-btn:checked { background: #E7DFCC; border-color: #B9B2A1; }
        .db-btn:disabled { color: #B3AD9E; background: #F8F7F2; }
        .db-mode { padding: 7px 18px; background: #FCFBF8;
                   border: 1px solid #C9C4B6; box-shadow: none;
                   font-size: 14px; color: #1A1916; }
        .db-mode-on { background: #1A1916; color: #FCFBF8;
                      border-color: #1A1916; }
        .db-go { padding: 9px 22px; background: #C8341E; background-image: none;
                 color: #FCFBF8; border: 1px solid #C8341E; border-radius: 8px;
                 box-shadow: none; font-size: 14px; font-weight: 600; }
        .db-go:hover { background: #B12D19; border-color: #B12D19; }
        .db-go:disabled { background: #C9C4B6; border-color: #C9C4B6;
                          color: #FCFBF8; }
        .db-row { padding: 9px 12px; }
        .db-row-sep { border-top: 1px solid #D7D2C5; }
        .db-name { font-size: 14px; color: #1A1916; }
        .db-meta { font-size: 12px; color: #6E695E; }
        .db-empty { font-size: 14px; color: #6E695E; }
        .db-status { padding: 7px 16px; font-size: 12px; color: #6E695E;
                     border-top: 1px solid #D7D2C5; background: #F8F7F2; }
        .db-prog { min-height: 10px; }
        .db-prog trough { min-height: 10px; background: #DED4C2;
                          border: 1px solid #D7D2C5; border-radius: 100px; }
        .db-prog progress { min-height: 10px; background-image: none;
                            background: #C8341E; border-radius: 100px;
                            border: none; }
        .db-meter { min-height: 8px; }
        .db-meter trough { min-height: 8px; background: #E7E2D6;
                           border: 1px solid #D7D2C5; }
        .db-meter progress { min-height: 8px; background-image: none;
                             background: #1A1916; border: none; }
        .db-meter-over progress { background: #C8341E; }
        .db-entry { background: #FCFBF8; border: 1px solid #C9C4B6;
                    padding: 6px 10px; font-size: 14px; color: #1A1916; }
        """
        try:
            prov = Gtk.CssProvider()
            prov.load_from_data(css)
            Gtk.StyleContext.add_provider_for_screen(
                self.get_screen(), prov,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        except Exception:                                   # noqa: BLE001
            pass          # styling is cosmetic; never block launch

    def _build(self):
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main.get_style_context().add_class("db-main")

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        col.set_margin_start(28)
        col.set_margin_end(28)
        col.set_margin_top(22)
        col.set_margin_bottom(10)

        head = Gtk.Label(label=_t("Disc Burner"), xalign=0)
        head.get_style_context().add_class("db-title")
        col.pack_start(head, False, False, 0)
        lede = Gtk.Label(label=_t("Put music on a CD, or video on a DVD."),
                         xalign=0)
        lede.get_style_context().add_class("db-lede")
        lede.set_margin_top(3)
        col.pack_start(lede, False, False, 0)

        # -- mode
        modes = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        modes.set_margin_top(18)
        self.audio_btn = Gtk.ToggleButton(label=_t("Music CD"))
        self.video_btn = Gtk.ToggleButton(label=_t("Video DVD"))
        for b, mode in ((self.audio_btn, self.AUDIO),
                        (self.video_btn, self.VIDEO)):
            b.get_style_context().add_class("db-mode")
            b.connect("clicked", self._on_mode, mode)
            modes.pack_start(b, False, False, 0)
        col.pack_start(modes, False, False, 0)

        # -- drive
        self._label(col, _t("DRIVE"))
        drow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        drow.get_style_context().add_class("db-field")
        drow.set_margin_top(6)
        dbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        dbox.set_margin_start(12)
        dbox.set_margin_end(12)
        dbox.set_margin_top(9)
        dbox.set_margin_bottom(9)
        self.drive_lbl = Gtk.Label(label="", xalign=0)
        self.drive_lbl.get_style_context().add_class("db-value")
        self.disc_lbl = Gtk.Label(label="", xalign=0)
        self.disc_lbl.get_style_context().add_class("db-meta")
        dbox.pack_start(self.drive_lbl, False, False, 0)
        dbox.pack_start(self.disc_lbl, False, False, 0)
        drow.pack_start(dbox, True, True, 0)
        self.rescan_btn = Gtk.Button(label=_t("Check again"))
        self.rescan_btn.get_style_context().add_class("db-btn")
        self.rescan_btn.set_valign(Gtk.Align.CENTER)
        self.rescan_btn.set_margin_end(12)
        self.rescan_btn.connect("clicked", lambda _b: self._rescan())
        drow.pack_start(self.rescan_btn, False, False, 0)
        col.pack_start(drow, False, False, 0)

        # -- disc name (the menu heading)
        self.name_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                spacing=0)
        self._label(self.name_box, _t("DISC NAME"))
        self.name_entry = Gtk.Entry()
        self.name_entry.get_style_context().add_class("db-entry")
        self.name_entry.set_text(_t("My Disc"))
        self.name_entry.set_margin_top(6)
        self.name_entry.connect("changed", lambda _e: self._refresh())
        self.name_box.pack_start(self.name_entry, False, False, 0)
        self.name_hint = Gtk.Label(
            label=_t("Shown at the top of the disc menu."), xalign=0)
        self.name_hint.get_style_context().add_class("db-hint")
        self.name_hint.set_margin_top(4)
        self.name_box.pack_start(self.name_hint, False, False, 0)
        col.pack_start(self.name_box, False, False, 0)

        # -- list
        self._label(col, _t("CONTENTS"))
        listwrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        listwrap.get_style_context().add_class("db-field")
        listwrap.set_margin_top(6)
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_size_request(-1, LIST_MIN_AUDIO)
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.scroll.add(self.rows)
        listwrap.pack_start(self.scroll, True, True, 0)
        col.pack_start(listwrap, True, True, 0)

        tools = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        tools.set_margin_top(8)
        self.add_btn = Gtk.Button(label=_t("Add…"))
        self.add_btn.get_style_context().add_class("db-btn")
        self.add_btn.connect("clicked", self._on_add)
        tools.pack_start(self.add_btn, False, False, 0)
        for label, delta in ((_t("Move up"), -1), (_t("Move down"), 1)):
            b = Gtk.Button(label=label)
            b.get_style_context().add_class("db-btn")
            b.connect("clicked", self._on_move, delta)
            tools.pack_start(b, False, False, 0)
            if delta < 0:
                self.move_up_btn = b
            else:
                self.move_down_btn = b
        self.clear_btn = Gtk.Button(label=_t("Remove all"))
        self.clear_btn.get_style_context().add_class("db-btn")
        self.clear_btn.connect("clicked", self._on_clear)
        tools.pack_end(self.clear_btn, False, False, 0)
        col.pack_start(tools, False, False, 0)

        # -- meter
        self.meter = Gtk.ProgressBar()
        self.meter.get_style_context().add_class("db-meter")
        self.meter.set_margin_top(14)
        col.pack_start(self.meter, False, False, 0)
        self.meter_lbl = Gtk.Label(label="", xalign=0)
        self.meter_lbl.get_style_context().add_class("db-hint")
        self.meter_lbl.set_margin_top(5)
        col.pack_start(self.meter_lbl, False, False, 0)
        self.warn = Gtk.Label(label="", xalign=0)
        self.warn.get_style_context().add_class("db-warn")
        self.warn.set_margin_top(4)
        self._wrap(self.warn)
        col.pack_start(self.warn, False, False, 0)

        # -- go
        go_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        go_row.set_margin_top(14)
        self.go_btn = Gtk.Button(label=_t("Burn disc"))
        self.go_btn.get_style_context().add_class("db-go")
        self.go_btn.connect("clicked", self._on_go)
        go_row.pack_start(self.go_btn, False, False, 0)
        self.stop_btn = Gtk.Button(label=_t("Stop"))
        self.stop_btn.get_style_context().add_class("db-btn")
        self.stop_btn.connect("clicked", self._on_stop)
        go_row.pack_start(self.stop_btn, False, False, 0)
        self.prog = Gtk.ProgressBar()
        self.prog.get_style_context().add_class("db-prog")
        self.prog.set_valign(Gtk.Align.CENTER)
        go_row.pack_start(self.prog, True, True, 0)
        col.pack_start(go_row, False, False, 0)

        main.pack_start(col, True, True, 0)
        self.status = Gtk.Label(label="", xalign=0)
        self.status.get_style_context().add_class("db-status")
        main.pack_end(self.status, False, False, 0)
        self.content.pack_start(main, True, True, 0)
        self.show_all()
        self.prog.hide()
        self.stop_btn.hide()

    def _label(self, box, text):
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.get_style_context().add_class("db-step")
        lbl.set_margin_top(20)
        box.pack_start(lbl, False, False, 0)
        return lbl

    @staticmethod
    def _wrap(label):
        label.set_line_wrap(True)
        label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_max_width_chars(1)
        return label

    # -- state ----------------------------------------------------------------
    def _on_mode(self, _btn, mode):
        if self.busy or self._add_pending or mode == self.mode:
            self._refresh()       # restore native checked state after a click
            return
        self._lists[self.mode] = self.items
        self.mode = mode
        self.items = self._lists[mode]
        self._sel = None          # an index into the list being left
        self._refresh()

    def _rescan(self):
        self.drives = optical_drives()
        self.drive = self.drives[0] if self.drives else None
        self._refresh()
        if self.drive:
            self._probe_disc()

    def _probe_disc(self):
        """Ask the drive what is in it, off the main loop: the query spins up
        the disc and can take seconds, and a window that freezes while it does
        looks broken."""
        node = self.drive["node"]
        self._jobs.start(
            "probe", lambda _job: disc_info(node),
            on_done=self._disc_answered,
            on_error=lambda _e: self._disc_answered(None),
            policy=nbjobs.REPLACE)

    def _disc_answered(self, info):
        self.disc = info or {}
        self._refresh()

    # The File menu hands these two straight to nbapp, which invokes a menu
    # callback as fn() with no arguments, while a toolbar button passes itself.
    # The default is what lets one handler serve both; without it File > Add…
    # and File > Remove All raised TypeError and did nothing at all.
    def _on_add(self, _btn=None):
        if self.busy or self._add_pending:
            return
        audio = self.mode == self.AUDIO
        start = MUSIC_DIR if audio else VIDEOS_DIR
        if not os.path.isdir(start):
            start = HOME
        pats = AUDIO_EXTS if audio else VIDEO_EXTS
        path = nbpicker.open_file(
            self, _t("Add to the disc"), start_dir=start,
            patterns=["*" + e for e in pats])
        if not path:
            return
        if self.mode == self.VIDEO and len(self.items) >= MAX_TITLES:
            self._say(_t("A disc menu holds %d titles.") % MAX_TITLES)
            return
        self._add_pending = True
        self._refresh()
        mode = self.mode
        job = self._jobs.start(
            "media-duration", lambda _job: media_duration(path),
            on_done=lambda seconds: self._duration_ready(path, mode, seconds),
            on_error=lambda _error: self._duration_ready(path, mode, None),
            policy=nbjobs.REJECT)
        if job is None:
            self._add_pending = False
            self._refresh()

    def _duration_ready(self, path, mode, seconds):
        """Install one picker result after its slow media probe completes."""
        self._add_pending = False
        if mode != self.mode:
            self._refresh()
            return
        self.items.append({"path": path,
                           "name": os.path.splitext(
                               os.path.basename(path))[0],
                           "seconds": seconds})
        self._refresh()

    def _on_move(self, _btn, delta):
        if self.busy or not self.items:
            return
        i = getattr(self, "_sel", None)
        if i is None or not 0 <= i < len(self.items):
            return
        j = i + delta
        if not 0 <= j < len(self.items):
            return
        self.items[i], self.items[j] = self.items[j], self.items[i]
        self._sel = j
        self._refresh()

    def _on_clear(self, _btn=None):
        if self.busy:
            return
        self.items = self._lists[self.mode] = []
        self._sel = None
        self._refresh()

    def _select(self, i):
        self._sel = i
        self._refresh()

    # -- rendering ------------------------------------------------------------
    def _burn_blocked(self):
        """Why the disc cannot be written right now, or "" when it can.

        Ordered the way somebody works: what is already happening, then what
        is missing from the list, then what is missing from the drive, then
        what is wrong with the contents.
        """
        if self.busy:
            return _t("The disc is being written.")
        if not self.items:
            return (_t("There are no songs to write.")
                    if self.mode == self.AUDIO
                    else _t("There are no videos to write."))
        if self.drive is None:
            return _t("This computer has no CD or DVD drive attached.")
        if getattr(self, "disc", {}).get("present") is not True:
            return _t("No disc in the drive.")
        if self._over_capacity():
            if self.mode == self.AUDIO:
                return _t("That is more than a CD holds. Remove a song or "
                          "two.")
            return _t("That is more video than one DVD holds at a watchable "
                      "quality. Remove a video or two.")
        bad = self._unreadable()
        if bad is not None:
            return (_t("“%s” cannot be read. Remove it to burn the disc.")
                    % bad["name"])
        return ""

    def _refresh(self):
        on = self.mode == self.AUDIO
        # The mode pair is ToggleButtons and _refresh is reached from their own
        # "clicked" handler (_on_mode, including its restore-native-state
        # branch), so a plain set_active here re-emits "clicked" and recurses
        # to RecursionError on every mode press. Restate them quietly.
        for b, want in ((self.audio_btn, on), (self.video_btn, not on)):
            nbapp.set_active_quietly(b, want)
            ctx = b.get_style_context()
            if want:
                ctx.add_class("db-mode-on")
            else:
                ctx.remove_class("db-mode-on")
        self.name_box.set_visible(not on)
        self.name_box.set_no_show_all(on)
        # A blank name field silently became "My Disc" on the disc itself, so
        # what the field said was not what the disc was called. The hint that
        # is already under it says the name it will get.
        self.name_hint.set_text(
            _t("Shown at the top of the disc menu.")
            if self.name_entry.get_text().strip()
            else _t("The disc will be named %s.") % _t("My Disc"))
        # The DISC NAME block only exists in Video DVD mode, and it has to be
        # paid for out of the list rather than out of the status bar.
        self.scroll.set_size_request(-1, LIST_MIN_AUDIO if on
                                     else LIST_MIN_VIDEO)
        writing = _t("The disc is being written.") if self.busy else ""
        set_reason(self.add_btn, writing or (_t("A file is being added.")
                                               if self._add_pending else ""))
        # Remove all declines the click while a disc is being written, so it
        # has to look declined — the File menu already greys the same action.
        set_reason(self.clear_btn, writing)

        # Rebuilding the list destroys the row widget holding the keyboard,
        # so the chosen row takes it back at the end — but ONLY when the
        # keyboard was in the list to begin with. _refresh also runs on every
        # keystroke in the DISC NAME field (its "changed" handler), and pulling
        # focus out of that field left the disc unnameable: the first letter
        # typed moved the keyboard to the chosen title and the rest went
        # nowhere.
        keyboard_in_list = any(self.get_focus() is btn for btn
                               in getattr(self, "_row_buttons", {}).values())

        for child in list(self.rows.get_children()):
            self.rows.remove(child)
        if not self.items:
            empty = Gtk.Label(
                label=(_t("No songs. Add one below.") if on
                       else _t("No videos. Add one below.")),
                xalign=0)
            empty.get_style_context().add_class("db-empty")
            empty.set_margin_start(12)
            empty.set_margin_top(16)
            empty.set_margin_bottom(16)
            self.rows.pack_start(empty, False, False, 0)
        for i, item in enumerate(self.items):
            self.rows.pack_start(self._item_row(i, item, first=(i == 0)),
                                 False, False, 0)
        self.rows.show_all()
        selected = getattr(self, "_sel", None)
        valid_selection = (isinstance(selected, int)
                           and 0 <= selected < len(self.items))
        if writing:
            up_reason = down_reason = writing
        elif not valid_selection:
            pick = (_t("Select a song to move it.") if on
                    else _t("Select a video to move it."))
            up_reason = down_reason = pick
        else:
            up_reason = (_t("This is already first in the list.")
                         if selected == 0 else "")
            down_reason = (_t("This is already last in the list.")
                           if selected >= len(self.items) - 1 else "")
        set_reason(self.move_up_btn, up_reason)
        set_reason(self.move_down_btn, down_reason)
        if valid_selection and keyboard_in_list:
            chosen = getattr(self, "_row_buttons", {}).get(selected)
            if chosen is not None:
                chosen.grab_focus()
        self._refresh_meter()
        self._refresh_drive()
        set_reason(self.go_btn, self._burn_blocked())

    def _item_row(self, i, item, first=False):
        btn = Gtk.ToggleButton()
        btn.get_style_context().add_class("db-btn")
        btn.set_active(i == getattr(self, "_sel", None))
        btn.get_accessible().set_name(item["name"])
        if not hasattr(self, "_row_buttons"):
            self._row_buttons = {}
        self._row_buttons[i] = btn
        btn.set_relief(Gtk.ReliefStyle.NONE)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.get_style_context().add_class("db-row")
        if not first:
            row.get_style_context().add_class("db-row-sep")
        num = Gtk.Label(label="%d" % (i + 1), xalign=0)
        num.get_style_context().add_class("db-meta")
        num.set_size_request(22, -1)
        row.pack_start(num, False, False, 0)
        name = Gtk.Label(xalign=0)
        _set_user_text(name, item["name"])
        name.get_style_context().add_class("db-name")
        name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        row.pack_start(name, True, True, 0)
        meta = Gtk.Label(label=(fmt_time(item["seconds"])
                                if item["seconds"] else _t("unreadable")),
                         xalign=1)
        meta.get_style_context().add_class("db-meta")
        row.pack_start(meta, False, False, 0)
        drop = Gtk.Button(label=_t("Remove"))
        drop.get_style_context().add_class("db-btn")
        drop.set_sensitive(not self.busy)
        drop.get_accessible().set_name("%s %s" % (_t("Remove"), item["name"]))
        drop.set_tooltip_text("%s %s" % (_t("Remove"), item["name"]))
        drop.connect("clicked", self._on_remove, i)
        row.pack_start(drop, False, False, 0)
        btn.add(row)
        btn.connect("clicked", lambda _b, n=i: self._select(n))
        return btn

    def _on_remove(self, _btn, i):
        if self.busy or not 0 <= i < len(self.items):
            return
        del self.items[i]
        self._sel = None
        self._refresh()

    def _unreadable(self):
        """The first listed file whose length could not be read, or None.

        A file the probe could not read is a file the decoder will not read
        either, so a burn that contains one runs every track before it and
        then fails — on a disc that is already being written, in the audio
        case. The row says "unreadable"; this is what stops the burn starting.
        """
        for item in self.items:
            if not item["seconds"]:
                return item
        return None

    def _durations(self):
        return [it["seconds"] or 0.0 for it in self.items]

    def _over_capacity(self):
        if not self.items:
            return False
        total = sum(self._durations())
        if self.mode == self.AUDIO:
            return total > CD_MAX_SECONDS
        return not dvd_fits(total)

    def _refresh_meter(self):
        total = sum(self._durations())
        over = self._over_capacity()
        if self.mode == self.AUDIO:
            frac = total / float(CD_MAX_SECONDS)
            self.meter_lbl.set_text(
                _t("%s of %s used") % (fmt_time(total),
                                       fmt_time(CD_MAX_SECONDS)))
        else:
            frac = total and min(1.0, total / (2.0 * 60 * 60)) or 0.0
            self.meter_lbl.set_text(
                _t("%s of video, %s") % (fmt_time(total),
                                         self._picture_quality(total))
                if total else "")
        self.meter.set_fraction(max(0.0, min(1.0, frac)))
        ctx = self.meter.get_style_context()
        if over:
            ctx.add_class("db-meter-over")
        else:
            ctx.remove_class("db-meter-over")
        if over and self.mode == self.AUDIO:
            self.warn.set_text(
                _t("That is more than a CD holds. Remove a song or two."))
        elif over:
            self.warn.set_text(
                _t("That is more video than one DVD holds at a watchable "
                   "quality. Remove a video or two."))
        else:
            bad = self._unreadable()
            self.warn.set_text(
                (_t("“%s” cannot be read. Remove it to burn the disc.")
                 % bad["name"]) if bad else "")

    @staticmethod
    def _picture_quality(total_seconds):
        """How the disc will look, said the way somebody watching it would.

        This line used to print the encoder's bitrate — "8000 kbit per
        second" — which is a number nobody watching a television can act on:
        it is solved from the running time and there is no control for it.
        What they can act on is how good the picture will be.
        """
        kbit = dvd_video_bitrate(total_seconds)
        if kbit >= DVD_PEAK_KBIT:
            return _t("best picture quality")
        if kbit > DVD_MIN_KBIT * 2:
            return _t("good picture quality")
        return _t("lower picture quality to fit")

    def _refresh_drive(self):
        if not self.drive:
            self.drive_lbl.set_text(_t("No disc drive"))
            self.disc_lbl.set_text(
                _t("This computer has no CD or DVD drive attached."))
            return
        self.drive_lbl.set_text(self.drive["label"])
        info = getattr(self, "disc", None) or {}
        if info.get("present") is False:
            self.disc_lbl.set_text(_t("No disc in the drive."))
        elif info.get("media"):
            parts = [info["media"]]
            if info.get("bytes"):
                parts.append(fmt_size(info["bytes"]) + " " + _t("free"))
            if info.get("blank") is False:
                parts.append(_t("already written"))
            self.disc_lbl.set_text(" - ".join(parts))
        elif info.get("present"):
            self.disc_lbl.set_text(_t("A disc is in the drive."))
        else:
            self.disc_lbl.set_text(self.drive["node"])

    def _say(self, text):
        self.status.set_text(text)

    # -- burning --------------------------------------------------------------
    def _on_go(self, _btn):
        if self.busy or not self.items or not self.drive:
            return
        if getattr(self, "disc", {}).get("present") is not True:
            self._say(_t("No disc in the drive."))
            return
        if not compatible_media(self.mode, self.disc):
            # The drive row already names the inserted medium, so this says why
            # that medium cannot be used and what to put in instead — rather
            # than the two-word name of the mode, which read as the button
            # having done nothing at all. The button itself stays live: a drive
            # that answers only half the probe (disc_info's normal case) must
            # still be allowed to try.
            self._say(self._wrong_medium())
            return
        missing = [n for n, ok in tools_present().items() if not ok]
        needed = (("ffmpeg", "wodim") if self.mode == self.AUDIO
                  else ("ffmpeg", "ffprobe", "dvdauthor", "spumux",
                        "genisoimage", "growisofs"))
        gone = [n for n in needed if n in missing]
        if gone:
            self._say(_t("This disc needs %s, which is not installed.")
                      % gone[0])
            return
        self._confirm()

    def _wrong_medium(self):
        """Why the disc in the drive cannot be written, and what to put in."""
        info = getattr(self, "disc", None) or {}
        if info.get("blank") is False:
            return _t("That disc has already been written. Put in a blank "
                      "one.")
        if self.mode == self.AUDIO:
            return _t("The disc in the drive cannot be used. A music CD needs "
                      "a blank CD-R or CD-RW.")
        return _t("The disc in the drive cannot be used. A video DVD needs a "
                  "blank DVD-R or DVD+R.")

    def _confirm(self):
        # WHOLE SENTENCES per count, not one string with a number in it: "1
        # songs" is wrong in English and a language's plural rule is not ours
        # to compose out of a format string.
        audio = self.mode == self.AUDIO
        one = len(self.items) == 1
        if audio:
            body = (_t("1 song will be written to the disc in the drive. "
                       "Anything already on it is lost.") if one else
                    _t("%d songs will be written to the disc in the drive. "
                       "Anything already on it is lost.") % len(self.items))
        else:
            body = (_t("1 video will be converted and written to the disc in "
                       "the drive. Anything already on it is lost.") if one
                    else
                    _t("%d videos will be converted and written to the disc "
                       "in the drive. Anything already on it is lost.")
                    % len(self.items))
        dlg = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text=_t("Write to the disc in %s?") % self.drive["label"])
        dlg.format_secondary_text(body)
        dlg.add_button(_t("Cancel"), Gtk.ResponseType.CANCEL)
        go = dlg.add_button(_t("Write the disc"), Gtk.ResponseType.OK)
        go.get_style_context().add_class("db-go")
        # A stray Return must not start a burn.
        dlg.set_default_response(Gtk.ResponseType.CANCEL)
        answer = dlg.run()
        dlg.destroy()
        if answer == Gtk.ResponseType.OK:
            self._start()

    def _start(self):
        total = sum(self._durations())
        if self.mode == self.VIDEO:
            want = scratch_needed(total)
            have = free_space(HOME)
            if have is not None and have < want:
                self._say(_t("Building a DVD needs about %s of free space "
                             "and there is %s.")
                          % (fmt_size(want), fmt_size(have)))
                return
        self.busy = True
        # Re-render with the burn running: Burn disc, Add…, Remove all and
        # every row's Remove are drawn from self.busy, and a control that will
        # decline the click has to look declined.
        self._refresh()
        set_reason(self.rescan_btn, _t("The disc is being written."))
        self.prog.set_fraction(0.0)
        self.prog.show()
        self.stop_btn.show()
        self.warn.set_text("")
        self._say(_t("Preparing…"))
        node = self.drive["node"]
        files = [it["path"] for it in self.items]
        names = [it["name"] for it in self.items]
        disc_title = self.name_entry.get_text().strip() or _t("My Disc")
        audio = self.mode == self.AUDIO
        standard = self.standard
        try:
            # In HOME, NOT /tmp, and that is not a preference: /tmp is a tmpfs
            # on this OS (see /etc/fstab), so a DVD build would try to hold
            # some nine gigabytes of transcodes, VIDEO_TS and ISO in RAM and
            # take the machine down with it. The dot keeps the scratch out of
            # the Finder while it exists; it is removed when the burn ends,
            # however it ends.
            self._workdir = tempfile.mkdtemp(prefix=".burner-", dir=HOME)
        except OSError:
            self.busy = False
            self._finished("error", _t("There is no room to prepare the disc."))
            return
        work = self._workdir

        def run(job):
            if audio:
                return build_audio_cd(job, node, files, work)
            return build_video_dvd(job, node, files, names, disc_title, work,
                                   standard)

        started = self._jobs.start(
            "burn", run,
            on_done=lambda _v: self._finished("done", ""),
            on_error=self._burn_error,
            on_cancel=lambda: self._finished("stopped", ""),
            on_progress=self._job_progress,
            policy=nbjobs.REJECT)
        if started is None:
            self._finished("error", _t("A disc is already being written."))

    def _job_progress(self, fraction, phase):
        if fraction is not None:
            self.prog.set_fraction(max(0.0, min(1.0, fraction)))
        if phase:
            self._say(phase)

    def _burn_error(self, error):
        # Matched on the exception CLASS, never on its words: the messages are
        # built with _t() and a translated string cannot be pattern-matched.
        kind = getattr(error, "kind", "")
        detail = getattr(error, "message", "") or _t("The disc was not written.")
        if kind in ("ToolMissing", "NoDisc", "DiscTooSmall", "NoRoom"):
            self._finished("error", detail)
        elif kind == "StepFailed":
            self._finished("error", detail)
        else:
            self._finished("error", _t("The disc was not written."))

    def _on_stop(self, _btn):
        if not self.busy:
            return
        self._say(_t("Stopping…"))
        self._jobs.cancel("burn")

    def _finished(self, how, message):
        self.busy = False
        if getattr(self, "_closing_after_stop", False):
            # Cancellation has now reached the worker's terminal callback, so
            # its external process group has been killed/reaped and the
            # workdir is no longer in use.  Only now may teardown close the
            # JobOwner and remove that directory.
            self._clean_workdir()
            self.destroy()
            return
        self.prog.hide()
        self.stop_btn.hide()
        set_reason(self.add_btn, "")
        set_reason(self.rescan_btn, "")
        self._clean_workdir()
        # A burn is a several-minute job and its result is a physical thing
        # somebody has to go and take out of the drive, so the outcome is also
        # left in the menu bar's notification centre — the one surface still on
        # screen once this window is behind whatever they moved on to.
        if how == "done":
            self._say(_t("The disc is written. It can be taken out."))
            self.notify(_t("The disc is written"),
                        _t("It can be taken out."))
        elif how == "stopped":
            self._say(_t("Stopped. The disc may be unusable."))
            self.notify(_t("The burn was stopped"),
                        _t("The disc may be unusable."))
        else:
            headline = _t("The disc was not written.")
            self._say(message or headline)
            # The tray shows title then body; an error with no sentence of its
            # own printed the same one twice.
            self.notify(headline, "" if message in (None, "", headline)
                        else message)
        self._refresh()
        if self.drive:
            self._probe_disc()

    # -- menus ----------------------------------------------------------------
    def menu_items(self, name):
        # Title Case and the accelerator spelled into the label, per
        # docs/MENU-CONVENTIONS.md. The toolbar buttons keep sentence case —
        # same actions, different rule: menus are Title Case, controls are not.
        # Entries stay PRESENT while a burn is running and go insensitive with
        # a None callback, so the menu does not shift under the user's hand.
        if name == "File":
            return [
                ("Add…", self._on_add
                 if not self.busy and not self._add_pending else None),
                ("Remove All", self._on_clear if not self.busy else None),
                nbapp.SEP,
                ("Close    Esc", self.close),
            ]
        return super().menu_items(name)

    def close(self, *_a):
        if DiscBurner._on_delete(self):
            return
        self.destroy()

    def _on_delete(self, *_a):
        """Route WM/menu/Escape close through active-burn cancellation."""
        if getattr(self, "_closing_after_stop", False):
            return True
        if self.busy and not self._confirm_stop_burn():
            return True
        if self.busy:
            self._closing_after_stop = True
            self._say(_t("Stopping…"))
            set_reason(self.stop_btn, _t("Stopping…"))
            set_reason(self.add_btn, _t("Stopping…"))
            set_reason(self.rescan_btn, _t("Stopping…"))
            self._jobs.cancel("burn")
            # Keep the window and JobOwner alive until _finished receives the
            # cancellation and performs the normal cleanup/destroy sequence.
            return True
        return False

    def _confirm_stop_burn(self):
        """Closing an active writer can leave its physical disc unusable.

        Headed the way USB Writer heads the same moment: the card has to say
        that a disc is being written and that leaving stops it, not repeat the
        word on its own button.
        """
        dlg = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text=_t("Stop writing?"))
        dlg.format_secondary_text(
            _t("The disc is only part-written. Stopping now may leave it "
               "unusable."))
        dlg.add_button(_t("Cancel"), Gtk.ResponseType.CANCEL)
        stop = dlg.add_button(_t("Stop writing"), Gtk.ResponseType.OK)
        stop.get_style_context().add_class("destructive-action")
        dlg.set_default_response(Gtk.ResponseType.CANCEL)
        answer = dlg.run()
        dlg.destroy()
        return answer == Gtk.ResponseType.OK


def main():
    DiscBurner()
    Gtk.main()


if __name__ == "__main__":
    main()
