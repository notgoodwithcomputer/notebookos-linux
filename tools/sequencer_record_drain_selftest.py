#!/usr/bin/env python3
"""Recorder pump drains queued PCM even after Stop has been requested."""

import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
import sequencer  # noqa: E402


class Out:
    def __init__(self, fd): self.fd = fd
    def fileno(self): return self.fd


class Proc:
    def __init__(self, fd):
        self.stdout = Out(fd)
        self.terminated = False
    def terminate(self): self.terminated = True


class Wav:
    def __init__(self): self.data = bytearray()
    def writeframes(self, chunk): self.data.extend(chunk)


class FullWav:
    def writeframes(self, _chunk):
        raise OSError("no space left")


def main():
    read_fd, write_fd = os.pipe()
    payload = bytes((i % 251 for i in range(6000)))
    os.write(write_fd, payload)
    os.close(write_fd)
    rec = sequencer.Recorder()
    rec.proc = Proc(read_fd)
    rec._wav = Wav()
    # This models Stop having been requested before the pump got CPU. The old
    # event-guarded loop wrote zero of the already-buffered bytes.
    rec._stop.set()
    try:
        rec._pump()
    finally:
        os.close(read_fd)
    ok = bytes(rec._wav.data) == payload and rec._frames == len(payload) // 2
    print(("PASS" if ok else "FAIL") + ": buffered PCM drains through EOF")

    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"\0" * 4096)
    os.close(write_fd)
    rec = sequencer.Recorder()
    proc = Proc(read_fd)
    rec.proc = proc
    rec._wav = FullWav()
    try:
        rec._pump()
    finally:
        os.close(read_fd)
    failed = rec.write_failed() and rec.failed_early() and proc.terminated
    print(("PASS" if failed else "FAIL") +
          ": WAV write failure stops capture and becomes terminal")

    all_ok = ok and failed
    print("RESULT: %s" % ("ALL PASS" if all_ok else "FAILED"))
    return not all_ok


if __name__ == "__main__":
    raise SystemExit(main())
