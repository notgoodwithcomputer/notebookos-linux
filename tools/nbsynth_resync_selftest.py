#!/usr/bin/env python3
"""Live song edits replace active WAV readers at the current frame."""

import array
import math
from pathlib import Path
import sys
import tempfile
import wave


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
import nbsynth  # noqa: E402


def song(wav=None, pan=0.0):
    clips = [] if wav is None else [{"s": 0.0, "e": 1.0, "wav": wav,
                                     "off": 0.0, "gain": 1.0,
                                     "fin": 0.0, "fout": 0.0}]
    return {"bpm": 120, "length": 1.0, "master": 1.0,
            "metronome": False, "fx": False, "tape": 0.0,
            "reverb": {"mix": 0.0, "size": 0.0},
            "delay": {"mix": 0.0, "time": 0.5, "feedback": 0.0},
            "loop": None, "tracks": [{"gain": 1.0, "pan": pan,
            "mute": False, "solo": False, "rev": 0.0, "dly": 0.0,
            "low": 0.0, "high": 0.0, "comp": 0.0, "clips": clips}]}


def main():
    with tempfile.TemporaryDirectory(prefix="nb-resync-") as td:
        path = str(Path(td) / "tone.wav")
        with wave.open(path, "wb") as out:
            out.setnchannels(1); out.setsampwidth(2); out.setframerate(nbsynth.SR)
            out.writeframes(array.array("h", [
                int(12000 * math.sin(i * 0.07)) for i in range(nbsynth.SR)
            ]).tobytes())
        mix = nbsynth.Mixdown(song(path), loop=False)
        before = mix.render(512)
        frame = mix.frame
        mix.resync(song(None))
        after = mix.render(512)
        ok1 = any(before) and not any(after) and not mix.audio
        mix2 = nbsynth.Mixdown(song(path), loop=False)
        mix2.render(512)
        frame2 = mix2.frame
        mix2.resync(song(path))
        ok2 = (mix2.frame == frame2 and len(mix2.audio) == 1
               and mix2.audio[0]["pos"] == frame2)
        mix.close(); mix2.close()
    print(("PASS" if ok1 else "FAIL") + ": deleted live clip stops next block")
    print(("PASS" if ok2 else "FAIL")
          + ": unchanged clip reopens at the current sample offset")
    print("RESULT: %s" % ("ALL PASS" if ok1 and ok2 else "FAILED"))
    return not (ok1 and ok2)


if __name__ == "__main__":
    raise SystemExit(main())
