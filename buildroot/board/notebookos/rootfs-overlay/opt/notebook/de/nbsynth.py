#!/usr/bin/env python3
"""
nbsynth — the Sequencer's audio engine: takes, effects, mix.

Pure Python, no GTK and no GStreamer, so the whole engine can be exercised on a
build host with nothing but the standard library: it takes a SONG (a plain dict
of tracks and clips) and turns it into signed 16-bit stereo PCM, either a block
at a time for live playback or straight into a .wav file for export.

WHAT IT IS AND IS NOT
---------------------
This is a RECORDING sequencer, and NOTHING here makes a sound of its own except
the metronome. The sound comes from the instruments in the room — a microphone
or an audio interface — and every clip on a track is a take of one. There are
no synthesised instruments and no drum machine: an instrument that never quite
sounds like the real one is worse than no instrument at all, and the point of
the app is that whoever is using it already has the thing they want to record.

So the engine's whole job is: read takes off the disk at the right moment, put
each one through its track's tone, compression, level and pan, feed the shared
room and echo, and add it up.

WHY IT IS WRITTEN THE WAY IT IS
-------------------------------
There is no numpy, and no audioop that both the guest (3.11) and the build host
(3.13, where audioop was removed) can share. Every sample therefore passes
through CPython, which makes the SHAPE of the inner loops the whole performance
story:

    for i in range(n): out[i] = out[i] + src[i] * g      ~34 ms / 48 000 frames
    [a + b * g for a, b in zip(out, src)]                ~ 5 ms / 48 000 frames

so every hot path here is a list comprehension over whole blocks and never an
indexed loop. Recursive filters (a one-pole, a comb's damping) cannot be
written that way, so they are restated as a delay line long enough that a whole
block can be read before any of it is written back, which is what makes the
reverb and the echo blockwise. The reverb runs at half the sample rate: a tail
is diffuse and dark by nature, so the top octave costs twice what it is worth.

WHAT A SONG LOOKS LIKE
----------------------
    {
      "bpm": 96, "length": 120.0,
      "master": 1.0, "metronome": False, "tape": 0.2,
      "reverb": {"mix": 0.35, "size": 0.7},
      "delay": {"mix": 0.3, "time": 0.75, "feedback": 0.32},  # time in beats
      "loop": [8.0, 24.0],                                    # or None
      "tracks": [
        {"gain": 1.0, "pan": 0.0, "mute": False,
         "solo": False, "rev": 0.2, "dly": 0.1,
         "low": 0.0, "high": 0.0, "comp": 0.0,
         "clips": [{"s": 8.0, "e": 12.0, "wav": "/path.wav", "off": 0.0,
                    "gain": 1.0, "fin": 0.01, "fout": 0.05}]},
        ...
      ]
    }

Every field is optional; normalize_song() fills in and clamps the rest, so a
hand-edited or truncated project can never raise inside the audio thread.
"""
import array
import itertools
import math
import operator
import os
import wave

# ---- format ---------------------------------------------------------------
SR = 48000            # sample rate, mono track internals / stereo output
CHANNELS = 2
BLOCK = 512           # frames per render block (~10.7 ms)
# Every delay line in the effects section must be at least one block long, so a
# whole block can be read out before any of it is written back — that is what
# lets the echo and the reverb be written as comprehensions instead of
# per-sample loops. Enforced in _Line.
MIN_LINE = BLOCK

# How long the reverb and the echo keep being run after the last thing was sent
# to them, in seconds. Long enough for the biggest room and the slowest echo to
# die away; see the tail note in Mixdown._render_block.
FX_TAIL = 6.0

# The reverb / echo sends a track starts with. A little room on everything is
# what makes eight dry close-miked takes sound like one recording rather than
# eight; the echo starts nearly off because it is a decision, not a default.
DEFAULT_SENDS = (0.18, 0.06)


def _exp_ramp(n, k):
    """exp(-k*i) for i in 0..n-1, built by repeated multiplication.

    Calling math.exp once per sample was the most expensive thing in the
    renderer. Each step is the previous one times a constant, and accumulate()
    does that in C."""
    if n <= 0:
        return []
    r = math.exp(-k)
    return list(itertools.accumulate([1.0] + [r] * (n - 1), operator.mul))


def _decay(n, t):
    """An exponential envelope roughly 35 dB down after t seconds."""
    return _exp_ramp(n, 4.0 / max(1e-3, t) / SR)


# ---------------------------------------------------------------------------
# per-track processing: tone and compression
# ---------------------------------------------------------------------------
def _cut_hz(amount, lo, hi):
    """Map a 0..1 control onto a corner frequency, logarithmically."""
    a = max(0.0, min(1.0, amount))
    return lo * ((hi / lo) ** a)


def apply_low_cut(buf, amount, st):
    """Take the rumble out of a track. One pole, state carried across blocks.

    A microphone in a bedroom picks up the desk, the floor, the traffic and the
    performer's own breath, all of it below anything musical and all of it
    stacking up across eight tracks. This is the single most useful thing that
    can be done to a recorded take, which is why it is on every strip."""
    f = _cut_hz(amount, 30.0, 320.0)
    a = 1.0 - math.exp(-2.0 * math.pi * f / SR)
    y = st
    out = [0.0] * len(buf)
    for i, x in enumerate(buf):
        y += a * (x - y)          # the low end...
        out[i] = x - y            # ...taken away
    return out, y


def apply_high_cut(buf, amount, st):
    """Take the top off a track — the other half of the tone control."""
    f = _cut_hz(1.0 - amount, 1200.0, 18000.0)
    a = 1.0 - math.exp(-2.0 * math.pi * min(f, SR * 0.45) / SR)
    y = st
    out = [0.0] * len(buf)
    for i, x in enumerate(buf):
        y += a * (x - y)
        out[i] = y
    return out, y


_COMP_CHUNK = 64


def apply_compress(buf, amount, st):
    """Even out the loud and quiet parts of a track.

    A one-knob compressor: more of it means a lower threshold, a harder ratio
    and more make-up together, so the control does what someone turning it up
    expects instead of needing three of them and a manual. The gain is worked
    out every 64 samples (1.3 ms) from that chunk's peak and RAMPED across the
    chunk, which is what keeps it from stepping audibly — a per-sample divide
    would cost several times as much for a difference nobody can hear on a
    vocal."""
    a = max(0.0, min(1.0, amount))
    thr = 0.5 - 0.44 * a
    ratio = 1.0 + 7.0 * a
    makeup = (1.0 / (thr + (1.0 - thr) / ratio)) ** 0.7
    atk = math.exp(-_COMP_CHUNK / (0.004 * SR))
    rel = math.exp(-_COMP_CHUNK / (0.15 * SR))
    env, g = st
    fresh = (env == 0.0 and g == 1.0)
    out = []
    for i in range(0, len(buf), _COMP_CHUNK):
        seg = buf[i:i + _COMP_CHUNK]
        if not seg:
            break
        pk = max(max(seg), -min(seg))
        if fresh:
            # Start from what is actually there. Starting from silence means
            # the first few milliseconds of a track go through at full make-up
            # gain before the envelope catches up, which is a click at the top
            # of every take.
            env = pk
            g = 1.0 if env <= thr else (thr + (env - thr) / ratio) / env
            fresh = False
        else:
            env = pk + (env - pk) * (atk if pk > env else rel)
        target = 1.0 if env <= thr else (thr + (env - thr) / ratio) / env
        d = (target - g) / len(seg)
        out.extend(x * (g + d * j) * makeup for j, x in enumerate(seg))
        g = target
    # ...and a limiter behind it. A compressor's attack is a real attack, so a
    # sharp onset does get through for a millisecond or two — times the make-up
    # gain, that is well past full scale. Rounding it off here is what every
    # compressor is used with anyway, and it keeps the control from being able
    # to make a track louder than the machine can play.
    return [x * (27.0 + x * x) / (27.0 + 9.0 * x * x) for x in out], (env, g)


# ---------------------------------------------------------------------------
# effects
# ---------------------------------------------------------------------------
class _Line:
    """A delay line at least one block long, read and written a block at a time.

    Reading at the write cursor returns the samples written exactly `n` frames
    ago, so the line's LENGTH is its delay. Because that length is never shorter
    than a block, a whole block can be read before any of it is written back —
    which is what allows the comb and the echo to be comprehensions rather than
    per-sample loops."""

    def __init__(self, n):
        self.n = max(MIN_LINE, int(n))
        self.buf = [0.0] * self.n
        self.p = 0

    def read(self, m):
        p, n, b = self.p, self.n, self.buf
        if p + m <= n:
            return b[p:p + m]
        k = n - p
        return b[p:] + b[:m - k]

    def write(self, data):
        m = len(data)
        p, n, b = self.p, self.n, self.buf
        if p + m <= n:
            b[p:p + m] = data
        else:
            k = n - p
            b[p:] = data[:k]
            b[:m - k] = data[k:]
        self.p = (p + m) % n

    def clear(self):
        self.buf = [0.0] * self.n
        self.p = 0


# Freeverb's tuning, halved: the reverb runs at SR/2, so the same delays in
# TIME are half as many samples. Four combs a side rather than eight — at half
# rate, with the allpass diffusion behind them, the extra pairs are not audible,
# and the reverb is the most expensive thing in the render.
_COMB_L = (558, 594, 638, 678)
_COMB_R = (569, 605, 649, 689)
_ALLP_L = (556, 441)
_ALLP_R = (569, 454)


class _Reverb:
    """A Schroeder/Freeverb room: parallel combs into series allpasses.

    Input is mono (the send bus); output is stereo, decorrelated by giving each
    side its own comb lengths."""

    def __init__(self):
        self.combL = [_Line(max(MIN_LINE, d)) for d in _COMB_L]
        self.combR = [_Line(max(MIN_LINE, d)) for d in _COMB_R]
        self.apL = [_Line(max(MIN_LINE, d)) for d in _ALLP_L]
        self.apR = [_Line(max(MIN_LINE, d)) for d in _ALLP_R]
        self.dampL = [0.0] * len(_COMB_L)
        self.dampR = [0.0] * len(_COMB_R)
        self.size = 0.7

    def clear(self):
        for ln in self.combL + self.combR + self.apL + self.apR:
            ln.clear()

    def _side(self, x, combs, aps, damp):
        acc = None
        fb = 0.62 + 0.35 * max(0.0, min(1.0, self.size))
        # A comb with feedback f settles at 1/(1-f) times its input, so a room
        # left at unity in comes out roughly ten times louder than what was sent
        # to it — and LOUDER the bigger the room, which is exactly the wrong way
        # round for a control someone is going to turn up. Scale the input by
        # (1-f) so the wet return sits near the level of the send whatever the
        # size is set to.
        x = [v * (2.3 * (1.0 - fb)) for v in x]
        for k, ln in enumerate(combs):
            d = ln.read(len(x))
            # a one-zero lowpass in the feedback path: the damping that stops a
            # long tail turning into a metallic ring, vectorised by carrying the
            # previous block's last sample rather than filtering per sample
            prev = damp[k]
            f = [(a + b) * 0.5 for a, b in zip(d, [prev] + d[:-1])]
            damp[k] = d[-1] if d else prev
            ln.write([a + b * fb for a, b in zip(x, f)])
            acc = d if acc is None else [a + b for a, b in zip(acc, d)]
        if acc is None:
            return [0.0] * len(x)
        g = 1.0 / len(combs)
        acc = [v * g for v in acc]
        for ln in aps:
            d = ln.read(len(acc))
            ln.write([a + b * 0.5 for a, b in zip(acc, d)])
            acc = [b - a for a, b in zip(acc, d)]
        return acc

    def process(self, mono):
        """mono block in (at SR) -> (L, R) at SR."""
        n = len(mono)
        half = mono[0::2]                        # decimate to SR/2
        if not half:
            return [0.0] * n, [0.0] * n
        L = self._side(half, self.combL, self.apL, self.dampL)
        R = self._side(half, self.combR, self.apR, self.dampR)
        return self._up(L, n), self._up(R, n)

    @staticmethod
    def _up(half, n):
        """Back to SR by linear interpolation — two taps, one comprehension."""
        out = [0.0] * n
        out[0::2] = half
        m = len(half)
        mid = [(half[i] + half[i + 1]) * 0.5 for i in range(m - 1)]
        if mid:
            out[1:2 * len(mid):2] = mid
        if 2 * m - 1 < n:
            out[2 * m - 1] = half[-1]
        return out


class _Echo:
    """A tempo-synced ping-pong delay.

    Left feeds right and right feeds left, so a single hit walks across the
    stereo field instead of thickening in the middle."""

    def __init__(self):
        self.L = _Line(MIN_LINE)
        self.R = _Line(MIN_LINE)
        self.samples = 0

    def set_time(self, samples):
        s = max(MIN_LINE, int(samples))
        if s != self.samples:
            self.samples = s
            self.L = _Line(s)
            self.R = _Line(s)

    def clear(self):
        self.L.clear()
        self.R.clear()

    def process(self, mono, feedback):
        n = len(mono)
        dl = self.L.read(n)
        dr = self.R.read(n)
        fb = max(0.0, min(0.92, feedback))
        self.L.write([x + d * fb for x, d in zip(mono, dr)])
        self.R.write([d * fb for d in dl])
        return dl, dr


class _Tape:
    """Wow, flutter and saturation — a worn cassette, in that order."""

    def __init__(self):
        self.L = _Line(2048)
        self.R = _Line(2048)
        self.ph = 0.0
        self.yl = 0.0
        self.yr = 0.0

    def clear(self):
        self.L.clear()
        self.R.clear()
        self.ph = 0.0

    def process(self, L, R, amount):
        n = len(L)
        a = max(0.0, min(1.0, amount))
        if a <= 0.001:
            return L, R
        # wow (0.7 Hz) plus flutter (6.3 Hz), as a slowly moving read offset
        self.ph += n / SR
        w = math.sin(2.0 * math.pi * 0.7 * self.ph) * 0.6 \
            + math.sin(2.0 * math.pi * 6.3 * self.ph) * 0.4
        off = int(1024 + w * 220 * a)
        self.L.write(L)
        self.R.write(R)
        dl = self.L.read(n)
        dr = self.R.read(n)
        k = max(0, min(len(dl) - 1, off - 1024 + n))
        dl = dl[k:] + dl[:k]
        dr = dr[k:] + dr[:k]
        drive = 1.0 + 2.2 * a
        # _soft(), inlined — see the master bus for why
        dl = [v * drive for v in dl]
        dl = [x * (27.0 + x * x) / (27.0 + 9.0 * x * x) for x in dl]
        dr = [v * drive for v in dr]
        dr = [x * (27.0 + x * x) / (27.0 + 9.0 * x * x) for x in dr]
        # Gentle high cut, as a two-point average rather than a one-pole: a
        # recursive filter is a per-sample Python loop, and this one would run
        # over the whole mix.
        c = 0.45 + 0.25 * a
        outl = [x * c + p * (1.0 - c)
                for x, p in zip(dl, [self.yl] + dl[:-1])]
        outr = [x * c + p * (1.0 - c)
                for x, p in zip(dr, [self.yr] + dr[:-1])]
        self.yl, self.yr = dl[-1], dr[-1]
        return ([x * (1 - a) + y * a for x, y in zip(L, outl)],
                [x * (1 - a) + y * a for x, y in zip(R, outr)])


def _soft(x):
    """A tanh-shaped limiter: linear where it matters, round where it clips."""
    x2 = x * x
    return x * (27.0 + x2) / (27.0 + 9.0 * x2)


# ---------------------------------------------------------------------------
# the song
# ---------------------------------------------------------------------------
def normalize_song(d):
    """Clamp an arbitrary dict into something the mixer can read blindly.

    Every value the audio thread touches is fixed here, once, so no block
    render can ever raise on a missing key, a string where a number belongs, or
    a negative length in a hand-edited project file."""
    d = d if isinstance(d, dict) else {}
    out = {
        "bpm": _cf(d.get("bpm"), 40, 240, 120),
        "length": _cf(d.get("length"), 1.0, 3600.0, 120.0),
        "master": _cf(d.get("master"), 0.0, 2.5, 1.0),
        "metronome": bool(d.get("metronome")),
        "tape": _cf(d.get("tape"), 0.0, 1.0, 0.0),
        "fx": bool(d.get("fx", True)),
    }
    rv = d.get("reverb") if isinstance(d.get("reverb"), dict) else {}
    out["reverb"] = {"mix": _cf(rv.get("mix"), 0.0, 1.0, 0.35),
                     "size": _cf(rv.get("size"), 0.0, 1.0, 0.7)}
    dl = d.get("delay") if isinstance(d.get("delay"), dict) else {}
    out["delay"] = {"mix": _cf(dl.get("mix"), 0.0, 1.0, 0.3),
                    "time": _cf(dl.get("time"), 0.0625, 4.0, 0.75),
                    "feedback": _cf(dl.get("feedback"), 0.0, 0.9, 0.32)}
    loop = d.get("loop")
    out["loop"] = None
    if isinstance(loop, (list, tuple)) and len(loop) == 2:
        a = _cf(loop[0], 0.0, 3600.0, 0.0)
        b = _cf(loop[1], 0.0, 3600.0, 0.0)
        if b - a > 0.05:
            out["loop"] = (a, b)
    tracks = []
    for t in (d.get("tracks") or []):
        t = t if isinstance(t, dict) else {}
        clips = []
        for c in (t.get("clips") or []):
            c = c if isinstance(c, dict) else {}
            s = _cf(c.get("s"), 0.0, 3600.0, 0.0)
            e = _cf(c.get("e"), 0.0, 3600.0, 0.0)
            if e <= s:
                continue
            wav = c.get("wav")
            wav = wav if isinstance(wav, str) and wav else None
            # A clip with no take behind it can never make a sound, so it is not
            # carried into the engine at all — an empty region is an arrangement
            # idea, and the arrangement is the app's business, not the mixer's.
            if not wav:
                continue
            clips.append({"s": s, "e": e, "wav": wav,
                          "off": _cf(c.get("off"), 0.0, 3600.0, 0.0),
                          "gain": _cf(c.get("gain"), 0.0, 4.0, 1.0),
                          "fin": _cf(c.get("fin"), 0.0, 30.0, 0.005),
                          "fout": _cf(c.get("fout"), 0.0, 30.0, 0.005)})
        tracks.append({
            "gain": _cf(t.get("gain"), 0.0, 2.0, 1.0),
            "pan": _cf(t.get("pan"), -1.0, 1.0, 0.0),
            "mute": bool(t.get("mute")),
            "solo": bool(t.get("solo")),
            "rev": _cf(t.get("rev"), 0.0, 1.0, 0.0),
            "dly": _cf(t.get("dly"), 0.0, 1.0, 0.0),
            "low": _cf(t.get("low"), 0.0, 1.0, 0.0),
            "high": _cf(t.get("high"), 0.0, 1.0, 0.0),
            "comp": _cf(t.get("comp"), 0.0, 1.0, 0.0),
            "clips": clips,
        })
    out["tracks"] = tracks
    return out


def _cf(v, lo, hi, default):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if f != f:                                  # NaN
        return default
    return max(lo, min(hi, f))


# ---------------------------------------------------------------------------
# the mixdown
# ---------------------------------------------------------------------------
class Mixdown:
    """Turns a song into stereo PCM, one block at a time.

    Created at a position (which may be NEGATIVE — that is the count-in, where
    the metronome sounds and nothing else has started yet), then pulled from
    with render(). The same object serves live playback and file export, so
    what is exported is exactly what was heard."""

    def __init__(self, song, start=0.0, metronome=None, loop=True):
        self.song = normalize_song(song)
        if metronome is not None:
            self.song["metronome"] = bool(metronome)
        self.use_loop = bool(loop)
        self.frame = int(round(float(start) * SR))
        self.events = []
        self.audio = []
        self._ei = 0
        self.reverb = _Reverb()
        self.echo = _Echo()
        self.tape = _Tape()
        self.overloaded = False
        self.peakL = 0.0
        self.peakR = 0.0
        self.track_peak = [0.0] * len(self.song["tracks"])
        self._tstate = {}
        self._fx_tail = 0
        self._solo = False
        self.wrapped = 0           # how many times the loop has come round
        self._schedule()
        self._catch_up()

    # -- scheduling ------------------------------------------------------
    def _schedule(self):
        """Flatten every clip in every track into one time-ordered event list.

        Doing it once, up front, keeps the per-block work proportional to what
        is SOUNDING rather than to the size of the arrangement."""
        ev = []
        for ti, tk in enumerate(self.song["tracks"]):
            for c in tk["clips"]:
                ev.append((int(c["s"] * SR), ti, c))
        ev.sort(key=lambda e: e[0])
        self.events = ev
        self._ei = 0

    def resync(self, song):
        """Adopt an edited arrangement without a gap in the sound."""
        self.song = normalize_song(song)
        self._schedule()
        f = self.frame
        while self._ei < len(self.events) and self.events[self._ei][0] < f:
            self._ei += 1

    def seek(self, seconds):
        self.frame = int(round(float(seconds) * SR))
        self._close_wavs()
        self._ei = 0
        self.reverb.clear()
        self.echo.clear()
        self.tape.clear()
        self._tstate = {}
        self._catch_up()

    def _loop_seek(self, seconds):
        """Jump back to the top of the loop WITHOUT cutting the sound.

        A seek clears the reverb, the echo and every track's filter state,
        which is right when the transport is moved by hand and wrong every
        time a loop comes round: the tail of the last bar is supposed to ring
        over the first bar of the next pass, and that is most of what makes a
        loop sound like music rather than like a tape splice."""
        self.frame = int(round(float(seconds) * SR))
        self._close_wavs()
        self._ei = 0
        self._catch_up()
        self.wrapped += 1

    def _catch_up(self):
        """Open every take that is already part-way through at this frame.

        A seek into the last chorus walks past every clip that finished minutes
        ago, so the CHEAP TEST — does this clip's span still reach the playhead
        — comes first and the file is only opened for the handful that do."""
        f = self.frame
        i = 0
        ev = self.events
        while i < len(ev) and ev[i][0] < f:
            fr, ti, c = ev[i]
            i += 1
            if fr + int((c["e"] - c["s"]) * SR) > f:
                self._open_wav(ti, c, (f - fr) / SR)
        self._ei = i

    def position(self):
        return self.frame / float(SR)

    # -- sources ---------------------------------------------------------
    def _open_wav(self, ti, clip, into=0.0):
        """Begin playing a take. `into` is how far into the CLIP to start."""
        try:
            w = wave.open(clip["wav"], "rb")
        except Exception:
            return
        try:
            ch = w.getnchannels()
            sw = w.getsampwidth()
            rate = w.getframerate()
            if sw != 2 or ch not in (1, 2) or rate <= 0:
                w.close()
                return
            start = int(max(0.0, clip["off"] + into) * rate)
            if start:
                w.setpos(min(start, w.getnframes()))
        except Exception:
            try:
                w.close()
            except Exception:
                pass
            return
        dur = clip["e"] - clip["s"]
        self.audio.append({
            "w": w, "ti": ti, "ch": ch, "rate": rate,
            "left": int(max(0.0, dur - into) * rate),
            "pos": int(max(0.0, into) * SR),        # frames into the clip
            "total": max(1, int(dur * SR)),
            "gain": clip["gain"],
            "fin": max(0, int(clip["fin"] * SR)),
            "fout": max(0, int(clip["fout"] * SR)),
        })

    def _close_wavs(self):
        for a in self.audio:
            try:
                a["w"].close()
            except Exception:
                pass
        self.audio = []

    def close(self):
        self._close_wavs()

    def _read_wav(self, a, n):
        """n frames of a playing take as floats, or None once it has run out.

        The clip's own gain and its fades are applied HERE, where the frames
        already are, rather than by another pass over the whole track: a fade
        is a handful of multiplies at one end of one clip, and a second buffer
        walk to do it would cost more than the fade itself."""
        if a["left"] <= 0:
            try:
                a["w"].close()
            except Exception:
                pass
            return None
        want = min(n, a["left"])
        try:
            raw = a["w"].readframes(want)
        except Exception:
            return None
        if not raw:
            return None
        buf = array.array("h")
        try:
            buf.frombytes(raw[:len(raw) - (len(raw) % 2)])
        except (ValueError, EOFError):
            return None
        if a["ch"] == 2:
            # A stereo file is summed, because a track is one channel until the
            # pan control puts it somewhere. Averaging both sides rather than
            # taking the left one keeps everything that was recorded.
            buf = array.array("h", [(x + y) // 2
                                    for x, y in zip(buf[0::2], buf[1::2])])
        a["left"] -= len(buf)
        g = a["gain"] * 3.0517578125e-05        # /32768
        pos = a["pos"]
        m = len(buf)
        fin, fout, total = a["fin"], a["fout"], a["total"]
        if (fin and pos < fin) or (fout and pos + m > total - fout):
            out = []
            for j, x in enumerate(buf):
                p = pos + j
                e = 1.0
                if fin and p < fin:
                    e = p / fin
                if fout and p > total - fout:
                    e *= max(0.0, (total - p) / fout)
                out.append(x * g * e)
        else:
            out = [x * g for x in buf]
        a["pos"] = pos + m
        return out

    # -- the block -------------------------------------------------------
    def _audible(self, tk):
        if tk["mute"]:
            return False
        if self._solo and not tk["solo"]:
            return False
        return True

    def render(self, n=BLOCK):
        """The next `n` frames as interleaved S16LE stereo bytes.

        A loop is handled HERE and not by the transport: the block that crosses
        the loop end is rendered in two pieces with the jump between them, so
        the loop point falls on the exact sample it is set to rather than on a
        block boundary ten milliseconds away, and the reverb and echo carry
        over the seam."""
        loop = self.song["loop"] if self.use_loop else None
        if loop:
            end = int(loop[1] * SR)
            if self.frame >= end:
                # the loop was moved (or turned on) while the playhead was
                # already past its end — come round at once rather than
                # running on to the end of the arrangement
                self._loop_seek(loop[0])
            elif self.frame + n >= end:
                # >=, NOT >. A block that ends EXACTLY on the loop point is the
                # common case, not a corner one: the loop is set to whole bars
                # and the block size divides into them, so an exclusive test
                # meant the playhead landed on the end and sailed straight over
                # it every time but the first.
                first = end - self.frame
                head = self._render_block(first)
                self._loop_seek(loop[0])
                return head + self._render_block(n - first)
        return self._render_block(n)

    def _render_block(self, n):
        if n <= 0:
            return b""
        song = self.song
        self._solo = any(t["solo"] for t in song["tracks"])
        nt = len(song["tracks"])
        f0 = self.frame
        f1 = f0 + n

        # 1. open every take that begins inside this block
        ev = self.events
        while self._ei < len(ev) and ev[self._ei][0] < f1:
            fr, ti, c = ev[self._ei]
            self._ei += 1
            if fr < f0:
                continue                     # handled by _catch_up / a seek
            # a take that starts mid-block is within 10 ms of the boundary,
            # which is not worth a second read path
            self._open_wav(ti, c)

        # 2. read every playing take into its track's accumulator
        acc = [None] * nt
        if self.audio:
            for a in list(self.audio):
                got = self._read_wav(a, n)
                if got is None:
                    self.audio.remove(a)
                    continue
                ti = a["ti"]
                t = acc[ti]
                if t is None:
                    t = acc[ti] = [0.0] * n
                m = min(n, len(got))
                if m == n:
                    acc[ti] = [x + y for x, y in zip(t, got)]
                else:
                    t[:m] = [x + y for x, y in zip(t[:m], got[:m])]

        # 3. per-track tone and compression, then out to the buses
        dryL = [0.0] * n
        dryR = [0.0] * n
        revS = None
        dlyS = None
        fx = song["fx"] and not self.overloaded
        peaks = [0.0] * nt
        for ti in range(nt):
            a = acc[ti]
            if a is None:
                continue
            tk = song["tracks"][ti]
            if not self._audible(tk):
                continue
            st = self._tstate.get(ti)
            if st is None:
                st = self._tstate[ti] = {"lo": 0.0, "hi": 0.0, "cp": (0.0, 1.0)}
            if tk["low"] > 0.005:
                a, st["lo"] = apply_low_cut(a, tk["low"], st["lo"])
            if tk["high"] > 0.005:
                a, st["hi"] = apply_high_cut(a, tk["high"], st["hi"])
            if tk["comp"] > 0.005:
                a, st["cp"] = apply_compress(a, tk["comp"], st["cp"])
            # The meter reads the track AFTER its own processing and before the
            # fader, which is where an engineer looks: it says what is going
            # into the mix, not what came off the disk.
            peaks[ti] = max(max(a), -min(a))
            g = tk["gain"]
            if g <= 0.0005:
                continue
            # equal-power pan: a track swept across the field keeps its level
            th = (tk["pan"] + 1.0) * 0.25 * math.pi
            gl = g * math.cos(th)
            gr = g * math.sin(th)
            dryL = [x + y * gl for x, y in zip(dryL, a)]
            dryR = [x + y * gr for x, y in zip(dryR, a)]
            if fx:
                rv = tk["rev"] * song["reverb"]["mix"]
                if rv > 0.002:
                    if revS is None:
                        revS = [0.0] * n
                    revS = [x + y * rv * g for x, y in zip(revS, a)]
                dv = tk["dly"] * song["delay"]["mix"]
                if dv > 0.002:
                    if dlyS is None:
                        dlyS = [0.0] * n
                    dlyS = [x + y * dv * g for x, y in zip(dlyS, a)]
        self.track_peak = peaks

        # 4. effects
        #
        # THE TAIL IS THE WHOLE POINT OF AN EFFECT LIKE THIS, and it outlives
        # its input by definition: the take that fed the room stops, and the
        # room goes on sounding. Running the reverb only on blocks that have
        # something to send it silences it at the exact moment it matters.
        if fx and (dlyS is not None or revS is not None):
            self._fx_tail = int(FX_TAIL * SR / n) + 1
        elif fx and self._fx_tail > 0:
            self._fx_tail -= 1
        else:
            self._fx_tail = 0
        if fx and self._fx_tail > 0:
            if dlyS is None:
                dlyS = [0.0] * n
            spb = 60.0 / song["bpm"]
            self.echo.set_time(int(spb * song["delay"]["time"] * SR))
            el, er = self.echo.process(dlyS, song["delay"]["feedback"])
            dryL = [x + y * 0.7 for x, y in zip(dryL, el)]
            dryR = [x + y * 0.7 for x, y in zip(dryR, er)]
            if revS is None:
                revS = [0.0] * n
            # the echo feeds the room too, which is what stops a long delay
            # sounding stuck in front of the mix
            revS = [x + (a + b) * 0.18 for x, a, b in zip(revS, el, er)]
            self.reverb.size = song["reverb"]["size"]
            rl, rr = self.reverb.process(revS)
            dryL = [x + y for x, y in zip(dryL, rl)]
            dryR = [x + y for x, y in zip(dryR, rr)]

        # 5. metronome, after the sends: a click belongs in the room the
        #    engineer is in, not in the record
        if song["metronome"]:
            click = self._metro(f0, n)
            if click is not None:
                dryL = [x + y for x, y in zip(dryL, click)]
                dryR = [x + y for x, y in zip(dryR, click)]

        # 6. master
        if fx and song["tape"] > 0.001:
            dryL, dryR = self.tape.process(dryL, dryR, song["tape"])
        m = song["master"]
        if abs(m - 1.0) > 0.001:
            dryL = [x * m for x in dryL]
            dryR = [x * m for x in dryR]
        # _soft(), inlined: two multiplies and a divide, and calling it 96 000
        # times a second costs more in Python call overhead than the arithmetic
        dryL = [x * (27.0 + x * x) / (27.0 + 9.0 * x * x) for x in dryL]
        dryR = [x * (27.0 + x * x) / (27.0 + 9.0 * x * x) for x in dryR]
        self.peakL = max(max(dryL), -min(dryL)) if dryL else 0.0
        self.peakR = max(max(dryR), -min(dryR)) if dryR else 0.0
        self.frame = f1
        return _pack(dryL, dryR)

    # -- the click -------------------------------------------------------
    _CLICK = {}

    def _click_buf(self, accent):
        got = self._CLICK.get(accent)
        if got is None:
            n = int(0.035 * SR)
            f = 1760.0 if accent else 1170.0
            d = _decay(n, 0.012)
            got = array.array("f", [
                math.sin(2.0 * math.pi * f * i / SR) * d[i]
                * (0.30 if accent else 0.18) for i in range(n)])
            self._CLICK[accent] = got
        return got

    def _metro(self, f0, n):
        """Mix the clicks that fall inside this block, count-in included.

        The click grid runs through NEGATIVE time as well, so a pre-roll before
        the punch-in point is counted in the song's own tempo by construction
        rather than by a separate timer that can drift away from it."""
        spb = 60.0 / self.song["bpm"]
        step = spb * SR
        if step < 8:
            return None
        first = math.ceil(f0 / step)
        last = math.floor((f0 + n - 1) / step)
        out = None
        b = first
        while b <= last:
            at = int(b * step) - f0
            if 0 <= at < n:
                buf = self._click_buf((int(b) % 4) == 0)
                if out is None:
                    out = [0.0] * n
                m = min(len(buf), n - at)
                out[at:at + m] = [x + y for x, y in zip(out[at:at + m],
                                                        buf[:m])]
            b += 1
        return out


def _pack(L, R):
    """Interleave and clamp two float blocks into S16LE stereo bytes."""
    n = len(L)
    out = array.array("h", bytes(n * 4))
    out[0::2] = array.array("h", [
        32767 if x > 1.0 else (-32767 if x < -1.0 else int(x * 32767.0))
        for x in L])
    out[1::2] = array.array("h", [
        32767 if x > 1.0 else (-32767 if x < -1.0 else int(x * 32767.0))
        for x in R])
    return out.tobytes()


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------
def song_tail(song):
    """Seconds of reverb and echo to keep rendering after the last sound.

    Exporting exactly to the arrangement's length chops the tail off the final
    bar, which is the one place a fade-out is supposed to live."""
    s = normalize_song(song)
    if not s["fx"]:
        return 0.35
    t = 0.6 + 2.6 * s["reverb"]["size"] * (1.0 if s["reverb"]["mix"] else 0.0)
    d = s["delay"]["mix"] and (60.0 / s["bpm"]) * s["delay"]["time"] * 4.0
    return max(0.35, min(8.0, max(t, d or 0.0)))


def render_wav(song, path, progress=None, cancel=None):
    """Render a whole song to a 16-bit stereo .wav. Returns the frames written.

    THE LOOP IS IGNORED here on purpose. A loop is a way of working on a
    section, not part of the arrangement, and a file that repeated bars 5 to 9
    for ever because the loop happened to be left on would be a surprise nobody
    asked for.

    `progress` is called with 0.0..1.0; `cancel` is polled and, if it returns
    true, the part-written file is removed and None is returned — an abandoned
    export must not leave a half-song behind that looks like a finished one."""
    s = normalize_song(song)
    total = int((s["length"] + song_tail(s)) * SR)
    mix = Mixdown(s, 0.0, metronome=False, loop=False)
    tmp = path + ".part"
    w = None
    try:
        w = wave.open(tmp, "wb")
        w.setnchannels(CHANNELS)
        w.setsampwidth(2)
        w.setframerate(SR)
        done = 0
        while done < total:
            n = min(BLOCK, total - done)
            w.writeframes(mix.render(n))
            done += n
            if cancel is not None and cancel():
                w.close()
                w = None
                os.unlink(tmp)
                return None
            if progress is not None and (done // BLOCK) % 16 == 0:
                progress(done / float(total))
        w.close()
        w = None
        os.replace(tmp, path)
        if progress is not None:
            progress(1.0)
        return total
    finally:
        mix.close()
        if w is not None:
            try:
                w.close()
            except Exception:
                pass
            try:
                os.unlink(tmp)
            except OSError:
                pass


def wav_info(path):
    """(seconds, channels, rate) for a wav, or None if it cannot be read."""
    try:
        with wave.open(path, "rb") as w:
            r = w.getframerate() or SR
            return w.getnframes() / float(r), w.getnchannels(), r
    except Exception:
        return None


# A tiny self-check so `python3 nbsynth.py` proves the engine on any machine.
# It has to make its own take first: nothing in here synthesises anything, so
# without a .wav on the disk there is by definition no sound to render.
if __name__ == "__main__":                                # pragma: no cover
    import time
    take = "/tmp/nbsynth-take.wav"
    n = int(3.0 * SR)
    with wave.open(take, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        env = _decay(n, 1.2)
        w.writeframes(array.array("h", [
            int(22000 * env[i] * math.sin(2 * math.pi * 220.0 * i / SR))
            for i in range(n)]).tobytes())
    song = {
        "bpm": 92, "length": 10.0, "tape": 0.2,
        "reverb": {"mix": 0.4, "size": 0.7},
        "tracks": [
            {"gain": 1.0, "rev": 0.25, "pan": -0.4, "low": 0.3,
             "clips": [{"s": 0.0, "e": 3.0, "wav": take, "fout": 0.25},
                       {"s": 4.0, "e": 7.0, "wav": take, "fout": 0.25}]},
            {"gain": 0.8, "dly": 0.5, "pan": 0.4, "comp": 0.6,
             "clips": [{"s": 2.0, "e": 5.0, "wav": take, "off": 0.5}]},
        ],
    }
    t0 = time.time()
    out = "/tmp/nbsynth-demo.wav"
    render_wav(song, out)
    dt = time.time() - t0
    info = wav_info(out)
    print("rendered %.1fs of audio in %.1fs (%.2fx real time) -> %s"
          % (info[0], dt, info[0] / dt, out))
