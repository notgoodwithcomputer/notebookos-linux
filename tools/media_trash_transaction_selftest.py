#!/usr/bin/env python3
"""Display-free transaction checks for Media Viewer trash."""
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
import media  # noqa: E402


with tempfile.TemporaryDirectory(prefix="nb-media-trash-") as root:
    source = os.path.join(root, "photo.jpg")
    Path(source).write_bytes(b"photo")
    old_home = media.HOME
    media.HOME = root
    viewer = media.MediaViewer.__new__(media.MediaViewer)
    viewer._siblings = [source]
    # The failure is reported in the status line under the stage, NOT by
    # replacing the picture with a notice card: nothing moved and the file is
    # still open, so taking it off the screen described a failure of the file
    # instead of a failure of the move.
    said = []
    notices = []
    viewer._flash = lambda message, **_kw: said.append(message)
    viewer._show_notice = lambda *args: notices.append(args)
    real_write = media.nbapp.atomic_write_text
    media.nbapp.atomic_write_text = lambda *_args, **_kw: (_ for _ in ()).throw(
        OSError("full"))
    try:
        viewer._do_trash(source)
    finally:
        media.nbapp.atomic_write_text = real_write
        media.HOME = old_home
    assert Path(source).read_bytes() == b"photo"
    assert said and not Path(root, ".Trash", "photo.jpg").exists()
    print("PASS origin-write failure leaves the source file in place")
    assert not notices, "the failed move must not take over the stage"
    print("PASS ...and says so without taking the picture off the stage")

print("RESULT: ALL PASS")
