#!/usr/bin/env python3
"""Overlapping exports own independent drafts and publish complete WAVs."""
import os
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(
    ROOT, "buildroot", "board", "notebookos", "rootfs-overlay",
    "opt", "notebook", "de"))
import nbsynth  # noqa: E402


class SilentMix:
    def __init__(self, *_args, **_kwargs):
        pass

    def render(self, frames):
        return bytes(frames * nbsynth.CHANNELS * 2)

    def close(self):
        pass


with tempfile.TemporaryDirectory(prefix="nbsynth-export-") as td:
    dest = os.path.join(td, "mix.wav")
    barrier = threading.Barrier(2)
    real_mix, real_replace = nbsynth.Mixdown, nbsynth.os.replace
    results = []

    def synchronized_replace(src, dst):
        barrier.wait(timeout=3)
        return real_replace(src, dst)

    def render():
        try:
            results.append(nbsynth.render_wav({"length": 1}, dest))
        except OSError:
            results.append(None)

    nbsynth.Mixdown = SilentMix
    nbsynth.os.replace = synchronized_replace
    try:
        workers = [threading.Thread(target=render) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5)
    finally:
        nbsynth.Mixdown = real_mix
        nbsynth.os.replace = real_replace

    assert all(not worker.is_alive() for worker in workers), "exports deadlocked"
    assert all(result is not None for result in results), results
    info = nbsynth.wav_info(dest)
    assert info is not None and info[1:] == (2, nbsynth.SR), info
    assert not [name for name in os.listdir(td) if name.endswith(".part")]

    # A publication failure happens after the WAV handle has closed; its owned
    # draft must still be removed rather than accumulating beside Documents.
    nbsynth.Mixdown = SilentMix
    nbsynth.os.replace = lambda *_args: (_ for _ in ()).throw(OSError("full"))
    try:
        try:
            nbsynth.render_wav({"length": 1}, dest)
        except OSError:
            pass
    finally:
        nbsynth.Mixdown = real_mix
        nbsynth.os.replace = real_replace
    assert not [name for name in os.listdir(td) if name.endswith(".part")]

print("NBSYNTH EXPORT LIFECYCLE SELFTEST: 5 checks, all pass")
