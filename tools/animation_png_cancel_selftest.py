#!/usr/bin/env python3
"""Cancelling PNG export must not publish partial frames as completed."""

from pathlib import Path
import os
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, os.fspath(DE))

import animation  # noqa: E402


class Cancel:
    calls = 0
    def is_set(self):
        self.calls += 1
        return self.calls > 1


class Image:
    def write_to_png(self, path):
        Path(path).write_bytes(b"png")


def main() -> None:
    old = animation.composite
    animation.composite = lambda *_args: Image()
    try:
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "final"
            try:
                animation.export_png_frames(None, [(0, 0), (0, 1), (0, 2)],
                                            os.fspath(output), Cancel())
            except InterruptedError:
                pass
            else:
                raise AssertionError("cancel returned as successful completion")
            assert not output.exists()
            assert not list(Path(td).glob(".frames-*"))
    finally:
        animation.composite = old
    print("PASS cancelled PNG exports publish no partial final directory")
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
