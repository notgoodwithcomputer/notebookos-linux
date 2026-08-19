#!/usr/bin/env python3
"""Editable Illustrator layers must survive Save/Open, not only flatten."""
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, os.fspath(DE))

import cairo  # noqa: E402
import illustrator  # noqa: E402


def paint(layer, r, g, b):
    cr = cairo.Context(layer.surface)
    cr.set_operator(cairo.OPERATOR_SOURCE)
    cr.set_source_rgb(r, g, b)
    cr.paint()
    layer.surface.flush()


with tempfile.TemporaryDirectory() as td:
    path = os.path.join(td, "drawing.png")
    app = illustrator.Illustrator.__new__(illustrator.Illustrator)
    app.cw = app.ch = 8
    bottom = illustrator.Layer("Ink", 8, 8)
    top = illustrator.Layer("Hidden guide", 8, 8)
    paint(bottom, 1, 0, 0)
    paint(top, 0, 0, 1)
    top.visible = False
    top.opacity = 37
    app.layers = [bottom, top]
    app.active = 1
    app.next_id = 9
    assert app._write_png(path)
    assert os.path.isfile(path)
    digest = app._file_digest(path)
    assert os.path.isfile(app._layer_sidecar(path, digest))

    restored = app._read_layer_sidecar(path, 8, 8)
    assert restored is not None
    layers, active, next_id = restored
    assert [x.name for x in layers] == ["Ink", "Hidden guide"]
    assert [x.visible for x in layers] == [True, False]
    assert [x.opacity for x in layers] == [100, 37]
    assert active == 1 and next_id == 9

    # Replacing the portable PNG outside Illustrator must never attach stale
    # editable layers to unrelated bytes at the same pathname.
    replacement = cairo.ImageSurface(cairo.FORMAT_ARGB32, 8, 8)
    paint_obj = illustrator.Layer("replacement", 8, 8)
    paint(paint_obj, 0, 1, 0)
    paint_obj.surface.write_to_png(path)
    assert app._read_layer_sidecar(path, 8, 8) is None

print("PASS layered Illustrator document survives and stale sidecars are rejected")
print("RESULT: ALL PASS")
