#!/usr/bin/env python3
"""Static contract checks for Notebook OS's shipped cursor theme."""

from pathlib import Path
import shutil
import struct
import tempfile

import cairo

import gen_cursors


ROOT = Path(__file__).resolve().parents[1]
CURSORS = (
    ROOT
    / "buildroot/board/notebookos/rootfs-overlay/usr/share/icons/notebook/cursors"
)
XCURSOR_MAGIC = 0x72756358
XCURSOR_IMAGE = 0xFFFD0002


def resolved(name: str, root: Path = CURSORS) -> Path:
    path = root / name
    assert path.exists(), f"missing cursor name: {name}"
    if path.is_symlink():
        link = Path(path.readlink())
        assert not link.is_absolute(), \
            f"cursor alias must be relative: {name} -> {link}"
    target = path.resolve(strict=True)
    assert target.parent == root.resolve(), \
        f"cursor escapes theme: {name} -> {target}"
    return target


def check_xcursor(path: Path) -> None:
    with path.open("rb") as fh:
        header = fh.read(16)
    assert len(header) == 16, f"truncated cursor: {path.name}"
    magic, header_size, _version, toc_count = struct.unpack("<4I", header)
    assert magic == XCURSOR_MAGIC, f"invalid Xcursor magic: {path.name}"
    assert header_size >= 16 and toc_count > 0, f"empty Xcursor: {path.name}"


def visual_signature(path: Path, wanted_size: int = 32):
    """Pixels and hotspot for one real Xcursor image, ignoring its filename."""
    data = path.read_bytes()
    magic, header_size, _version, toc_count = struct.unpack_from("<4I", data)
    assert magic == XCURSOR_MAGIC
    for index in range(toc_count):
        kind, subtype, position = struct.unpack_from(
            "<3I", data, header_size + index * 12)
        if kind != XCURSOR_IMAGE or subtype != wanted_size:
            continue
        chunk_header, chunk_kind, chunk_size, _chunk_version = \
            struct.unpack_from("<4I", data, position)
        assert chunk_kind == XCURSOR_IMAGE and chunk_size == wanted_size
        width, height, xhot, yhot, _delay = struct.unpack_from(
            "<5I", data, position + 16)
        pixels_at = position + chunk_header
        pixels = data[pixels_at:pixels_at + width * height * 4]
        assert len(pixels) == width * height * 4
        return width, height, xhot, yhot, pixels
    raise AssertionError(f"{path.name} has no {wanted_size}px cursor image")


def visually_distinct(left: Path, right: Path) -> bool:
    return visual_signature(left) != visual_signature(right)


def check_zoom_plus() -> None:
    """The even-odd lens must not cancel the overlapping centre of the +."""
    with tempfile.TemporaryDirectory() as td:
        for size in gen_cursors.SIZES:
            path = Path(td) / f"zoom-in-{size}.png"
            gen_cursors.render_png(gen_cursors.shape_zoom_in, size, str(path))
            surface = cairo.ImageSurface.create_from_png(str(path))
            data = surface.get_data()
            stride = surface.get_stride()
            x = y = round(0.42 * size)
            blue, green, red, alpha = data[y * stride + x * 4:y * stride + x * 4 + 4]
            assert alpha > 240 and max(red, green, blue) < 80, (
                f"zoom-in plus has a hollow centre at {size}px"
            )


def main() -> None:
    for path in CURSORS.iterdir():
        check_xcursor(resolved(path.name))

    busy = resolved("watch")
    for name in ("wait", "left_ptr_watch"):
        assert resolved(name) == busy, f"{name} must use the busy cursor"
    assert visually_distinct(busy, resolved("left_ptr")), \
        "busy cursor must differ from default"
    progress = resolved("progress")
    assert visually_distinct(progress, busy), \
        "interactive progress must differ from blocked wait"
    assert visually_distinct(progress, resolved("left_ptr")), \
        "progress must show activity"

    copy = resolved("copy")
    assert resolved("dnd-copy") == copy
    assert visually_distinct(copy, resolved("left_ptr")), \
        "copy drag needs positive feedback"
    assert resolved("dnd-move") == resolved("fleur")
    for name in ("alias", "link", "dnd-link"):
        assert resolved(name) == resolved("hand2")

    standard = ("context-menu", "help", "vertical-text", "zoom-in", "zoom-out")
    for name in standard:
        assert resolved(name).name == name, f"{name} needs its own semantic glyph"
    assert visually_distinct(resolved("zoom-in"), resolved("zoom-out"))

    # Red-proof the distinction check itself: another regular filename with
    # identical cursor bytes must still be recognized as the same visual.
    with tempfile.TemporaryDirectory() as td:
        impostor = Path(td) / "progress"
        shutil.copyfile(busy, impostor)
        assert not visually_distinct(impostor, busy), \
            "byte-identical cursor mutant escaped pixel comparison"
    check_zoom_plus()

    print("cursor contract: PASS")


if __name__ == "__main__":
    main()
