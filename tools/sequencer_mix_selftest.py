#!/usr/bin/env python3
"""
Every mixer control must be audible in the rendered file.

The Sequencer's strip carries pan, gain, mute and solo per track, plus a master
fader. Each is a control whose failure is SILENT in the worst sense: the slider
moves, the number changes, the project saves — and the sound does not. Nothing
on screen can tell you which of those happened. (ROADMAP #21 asserted exactly
this of the pan sliders, on the grounds that "the engine is mono". It is not:
`nbsynth.CHANNELS = 2` and `Mixdown` implements an equal-power pan. Measured,
the claim is false — which is why this suite measures rather than reads.)

Method: render the same two-bar song through the real `nbsynth.render_wav`,
changing one control at a time, and compare the RMS energy of the two channels
in the resulting 16-bit WAV. No mocks anywhere — the assertions are about what
came out of the encoder. The source is a real take on the disk, because that
is the only thing this engine makes a sound from.

Run:
    tools/guestrun.sh python3 tools/sequencer_mix_selftest.py
    tools/guestrun.sh python3 tools/sequencer_mix_selftest.py --de DIR
"""
import os
import sys
import math
import wave
import array
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.join(os.path.dirname(HERE), "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
if "--de" in sys.argv:
    DE = os.path.abspath(sys.argv[sys.argv.index("--de") + 1])
sys.path.insert(0, DE)

import nbsynth  # noqa: E402

TD = tempfile.mkdtemp(prefix="nb-mix-")
FAILED, N = [], [0]


def tone(name, amp=0.6, freq=220.0, seconds=2.0):
    """A take on the disk. Nothing in the engine synthesises anything, so
    every one of these tests needs a real recording to mix."""
    path = os.path.join(TD, name + ".wav")
    n = int(seconds * nbsynth.SR)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(nbsynth.SR)
        w.writeframes(array.array("h", [
            int(amp * 32000 * math.sin(2 * math.pi * freq * i / nbsynth.SR))
            for i in range(n)]).tobytes())
    return path


TAKE = None                     # filled in by main(), once nbsynth is loaded


def check(name, cond, detail=""):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name + (("  " + detail) if detail else ""))
    if not cond:
        FAILED.append(name)
    return bool(cond)


def not_reached(reason, *names):
    for n in names:
        check("%s  [not reached: %s]" % (n, reason), False)


def track(pan=0.0, gain=1.0, mute=False, solo=False, wav=None, clip_gain=1.0):
    return {"gain": gain, "pan": pan, "mute": mute,
            "solo": solo, "rev": 0.0, "dly": 0.0, "low": 0.0, "high": 0.0,
            "comp": 0.0,
            "clips": [{"s": 0.0, "e": 2.0, "wav": wav or TAKE,
                       "gain": clip_gain, "fin": 0.0, "fout": 0.0}]}


def song(tracks, master=1.0):
    return {"bpm": 120, "length": 2.0, "master": master,
            "metronome": False, "tape": 0.0, "fx": False,
            "reverb": {"mix": 0.0, "size": 0.0},
            "delay": {"mix": 0.0, "time": 0.5, "feedback": 0.0},
            "loop": None, "tracks": tracks}


def rms_lr(spec, name):
    """Render and return (rmsL, rmsR) of the produced file."""
    path = os.path.join(TD, name + ".wav")
    nbsynth.render_wav(spec, path)
    w = wave.open(path, "rb")
    ch, n = w.getnchannels(), w.getnframes()
    a = array.array("h")
    a.frombytes(w.readframes(n))
    w.close()
    if ch != 2:
        return None, None
    L, R = a[0::2], a[1::2]

    def rms(v):
        return math.sqrt(sum(x * x for x in v) / max(1, len(v)))
    return rms(L), rms(R)


def main():
    global TAKE
    print("rendering through the real nbsynth (%s)\n" % nbsynth.__file__)
    TAKE = tone("take")

    # ---- 0. the file really is stereo --------------------------------
    l0, r0 = rms_lr(song([track()]), "centre")
    stereo = check("the engine writes a stereo file", l0 is not None)
    if not stereo:
        not_reached("the render was not two-channel",
                    "hard left puts the sound in the left channel",
                    "hard right puts it in the right",
                    "centre is equal in both",
                    "centre obeys the equal-power law")
        return 1
    audible = check("a track at centre is audible", l0 > 100,
                    "L rms=%.0f" % l0)
    if not audible:
        not_reached("nothing was rendered to measure",
                    "hard left puts the sound in the left channel",
                    "hard right puts it in the right")
        return 1

    # ---- 1. pan ------------------------------------------------------
    ll, lr = rms_lr(song([track(pan=-1.0)]), "left")
    rl, rr = rms_lr(song([track(pan=1.0)]), "right")
    check("hard left puts the sound in the left channel", ll > 100 and lr < 1,
          "L=%.0f R=%.0f" % (ll, lr))
    check("hard right puts it in the right", rr > 100 and rl < 1,
          "L=%.0f R=%.0f" % (rl, rr))
    check("centre is equal in both", abs(l0 - r0) < max(1.0, l0 * 0.01),
          "L=%.0f R=%.0f" % (l0, r0))
    # Equal power means centre sits at cos(pi/4) of a hard-panned side, not at
    # half. A linear pan law would read ~0.50 here and would be a real defect:
    # a track swept across the field would dip in the middle.
    ratio = l0 / ll if ll > 1 else 0.0
    check("centre obeys the equal-power law", 0.66 < ratio < 0.78,
          "centre/hard = %.3f (equal-power = %.3f)" % (ratio, math.sqrt(0.5)))

    # ---- 2. gain and master ------------------------------------------
    ql, _qr = rms_lr(song([track(gain=0.5)]), "halfgain")
    check("halving a track's gain halves its level",
          0.45 < (ql / l0) < 0.55, "ratio=%.3f" % (ql / l0))
    ml, _mr = rms_lr(song([track()], master=0.5), "halfmaster")
    check("halving the master halves the mix",
          0.45 < (ml / l0) < 0.55, "ratio=%.3f" % (ml / l0))

    # ---- 3. mute and solo --------------------------------------------
    xl, xr = rms_lr(song([track(mute=True)]), "muted")
    check("a muted track makes no sound", xl < 1 and xr < 1,
          "L=%.0f R=%.0f" % (xl, xr))

    # Two tracks, one soloed: only the soloed one may be heard. They are panned
    # hard apart so the channels say which survived — a level check alone could
    # not tell "solo worked" from "both muted".
    both = song([track(pan=-1.0), track(pan=1.0)])
    bl, br = rms_lr(both, "both")
    check("two tracks panned apart fill both channels", bl > 100 and br > 100,
          "L=%.0f R=%.0f" % (bl, br))
    soloed = song([track(pan=-1.0), track(pan=1.0, solo=True)])
    sl, sr = rms_lr(soloed, "solo")
    check("solo silences the track that is not soloed", sl < 1,
          "L=%.0f R=%.0f" % (sl, sr))
    check("and keeps the one that is", sr > 100, "R=%.0f" % sr)

    # ---- 4. the VU meters read the actual audio ----------------------
    # ROADMAP #32 says they are `sin(tick)` — bouncing identically whether the
    # mic is live, muted or absent. They are measured off the block now, and
    # this asserts it the only way that can fail against that defect.
    #
    # The obvious test — loud track vs MUTED track — is vacuous: a muted track
    # never reaches the metering line at all (`_audible` skips it first), so
    # its peak is zero whatever the meter does. Planting a real sin(tick) meter
    # passed that check. Two AUDIBLE tracks holding recordings at very
    # different levels is the comparison a generated waveform cannot fake. The
    # meter is pre-fader (see nbsynth's own comment), so the track's gain is no
    # good here either — the SOURCE level has to differ, which now means two
    # different takes on the disk.
    def peaks_for(spec):
        mix = nbsynth.Mixdown(nbsynth.normalize_song(spec), 0.0,
                              metronome=False, loop=False)
        best = [0.0] * len(spec["tracks"])
        for _ in range(24):
            mix.render(nbsynth.BLOCK)
            for i, v in enumerate(getattr(mix, "track_peak", []) or []):
                best[i] = max(best[i], v)
        return best

    loud_take = tone("loud", amp=0.8)
    quiet_take = tone("quiet", amp=0.05)
    pk = peaks_for(song([track(pan=-1.0, wav=loud_take),
                         track(pan=1.0, wav=quiet_take)]))
    got = check("both tracks report a meter reading", len(pk) == 2 and min(pk) > 0,
                "peaks=%s" % ["%.3f" % v for v in pk])
    if got:
        # The two takes are 0.8 and 0.05 of full scale — a 16x spread that a
        # meter driven by anything other than the audio cannot reproduce.
        check("a loud track reads much higher than a quiet one",
              pk[0] > pk[1] * 8.0,
              "loud=%.3f quiet=%.3f (ratio %.1f, sources differ by 16x)"
              % (pk[0], pk[1], pk[0] / max(1e-9, pk[1])))
        check("the quiet track is not pinned at zero either",
              pk[1] > 0.001, "quiet=%.3f" % pk[1])
    else:
        not_reached("no meter readings came back",
                    "a loud track reads much higher than a quiet one",
                    "the quiet track is not pinned at zero either")

    # ---- 5. the clip's own level, and normalising twice -------------
    # THERE IS ALWAYS A SECOND PASS: render_wav normalises and then hands the
    # result to Mixdown, which normalises again. A field that survives one pass
    # and not two is a control that works when it is measured and not when it
    # is used, which is how every drum accent in every exported file was once
    # flattened to the default.
    cl, _cr = rms_lr(song([track(clip_gain=0.5)]), "clipgain")
    check("a clip's own level reaches the file",
          0.45 < (cl / l0) < 0.55, "ratio=%.3f" % (cl / l0))
    raw = song([track(clip_gain=0.5)])
    once = nbsynth.normalize_song(raw)
    twice = nbsynth.normalize_song(once)
    check("normalize_song is idempotent",
          once["tracks"] == twice["tracks"])
    check("a clip's level survives the round trip",
          twice["tracks"][0]["clips"][0]["gain"] == 0.5,
          "%s" % twice["tracks"][0]["clips"][0]["gain"])

    print("\n%d checks, %d passed, %d FAILED"
          % (N[0], N[0] - len(FAILED), len(FAILED)))
    if FAILED:
        print("RESULT: FAILED")
        for f in FAILED:
            print("  " + f)
        return 1
    print("RESULT: ALL PASS")
    return 0


try:
    rc = main()
finally:
    shutil.rmtree(TD, ignore_errors=True)
sys.exit(rc)
