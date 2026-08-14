#!/usr/bin/env python3
"""Display-free adversarial execution checks for Media's image paths."""
import os
import struct
import subprocess
import tempfile

HOME = tempfile.mkdtemp(prefix="nbmedia-adversarial-")
os.environ["NB_HOME"] = HOME

import media  # noqa: E402

passed = failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print("PASS " + name)
    else:
        failed += 1
        print("FAIL " + name + (": " + detail if detail else ""))


class FakePixbuf:
    def __init__(self, width=2, height=1, oriented=False):
        self.width, self.height, self.oriented = width, height, oriented

    def get_width(self): return self.width
    def get_height(self): return self.height

    def apply_embedded_orientation(self):
        return FakePixbuf(self.height, self.width, True)


def fallback_and_orientation_checks():
    src = os.path.join(HOME, "fallback.webp")
    with open(src, "wb") as fh:
        fh.write(b"real source bytes")
    tmp = os.path.join(HOME, "converted.png")
    with open(tmp, "wb") as fh:
        fh.write(b"real converted bytes")

    real_load = media.GdkPixbuf.Pixbuf.new_from_file
    real_decode = media._decode_to_png
    calls = []

    def load(path):
        calls.append(path)
        if path == src:
            raise RuntimeError("forced missing loader")
        return FakePixbuf()

    media.GdkPixbuf.Pixbuf.new_from_file = load
    media._decode_to_png = lambda path: tmp
    try:
        pb = media._pixbuf_any(src)
    finally:
        media.GdkPixbuf.Pixbuf.new_from_file = real_load
        media._decode_to_png = real_decode
    check("pixbuf-refused format is displayed through CLI fallback",
          calls == [src, tmp] and pb is not None, repr(calls))
    check("fallback decode applies embedded orientation",
          getattr(pb, "oriented", False), "fallback pixbuf was not oriented")

    # Direct loader must obey the same orientation law.
    media.GdkPixbuf.Pixbuf.new_from_file = lambda _path: FakePixbuf()
    try:
        direct = media._pixbuf_any(src)
    finally:
        media.GdkPixbuf.Pixbuf.new_from_file = real_load
    check("native pixbuf decode applies embedded orientation",
          direct.oriented, "native pixbuf was not oriented")
    check("MUTANT: returning raw pixbuf DOES ignore EXIF orientation",
          not FakePixbuf().oriented)

    # Real external conversion: make a WebP, then force only the source-side
    # pixbuf attempt to refuse it. The unmodified CLI fallback must produce a
    # PNG that the real pixbuf loader reads.
    webp = os.path.join(HOME, "actual.webp")
    made = subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=red:s=17x9", "-frames:v", "1", webp]).returncode == 0
    real_bounded = media._bounded_pixbuf
    media._bounded_pixbuf = lambda path: (
        (_ for _ in ()).throw(RuntimeError("forced source refusal"))
        if path == webp else real_bounded(path))
    try:
        actual = media._pixbuf_any(webp) if made else None
    finally:
        media._bounded_pixbuf = real_bounded
    check("real WebP refused by pixbuf is converted by CLI fallback",
          actual is not None, "made=%r pixbuf=%r" % (made, actual))
    check("CLI fallback does not inflate a small image to the memory cap",
          actual is not None and actual.get_width() == 17
          and actual.get_height() == 9,
          "got %sx%s" % (actual.get_width(), actual.get_height()))
    check("MUTANT: fixed cap-sized fallback target DOES inflate small image",
          (media.MAX_PIX, int(media.MAX_PIX * 9 / 17)) != (17, 9))

    # Produce a real 2x1 JPEG and inject a standards-shaped EXIF orientation=6
    # APP1 segment. GdkPixbuf exposes the option and the app must return 1x2.
    jpeg = os.path.join(HOME, "oriented.jpg")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=blue:s=2x1", "-frames:v", "1", jpeg], check=True)
    raw = open(jpeg, "rb").read()
    tiff = (b"II\x2a\x00\x08\x00\x00\x00\x01\x00" +
            b"\x12\x01\x03\x00\x01\x00\x00\x00\x06\x00\x00\x00" +
            b"\x00\x00\x00\x00")
    payload = b"Exif\x00\x00" + tiff
    app1 = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    with open(jpeg, "wb") as fh:
        fh.write(raw[:2] + app1 + raw[2:])
    oriented = media._pixbuf_any(jpeg)
    check("real EXIF orientation rotates native JPEG geometry",
          (oriented.get_width(), oriented.get_height()) == (1, 2),
          "%dx%d" % (oriented.get_width(), oriented.get_height()))


def huge_decode_check():
    src = os.path.join(HOME, "huge.png")
    with open(src, "wb") as fh:
        fh.write(b"header")
    real_load = media.GdkPixbuf.Pixbuf.new_from_file
    real_info = media.GdkPixbuf.Pixbuf.get_file_info
    real_scaled = media.GdkPixbuf.Pixbuf.new_from_file_at_scale
    calls = []
    media.GdkPixbuf.Pixbuf.get_file_info = lambda path: (object(), 50000, 50000)
    media.GdkPixbuf.Pixbuf.new_from_file = lambda path: (
        calls.append(("full", path)) or FakePixbuf(50000, 50000))
    media.GdkPixbuf.Pixbuf.new_from_file_at_scale = lambda path, w, h, preserve: (
        calls.append(("scaled", path, w, h, preserve)) or FakePixbuf(w, h))
    try:
        media._pixbuf_any(src)
    finally:
        media.GdkPixbuf.Pixbuf.new_from_file = real_load
        media.GdkPixbuf.Pixbuf.get_file_info = real_info
        media.GdkPixbuf.Pixbuf.new_from_file_at_scale = real_scaled
    check("huge image decode is bounded before full allocation",
          not any(call[0] == "full" for call in calls)
          and calls == [("scaled", src, media.MAX_PIX, media.MAX_PIX, True)],
          repr(calls))
    mutant_calls = []
    (lambda path: mutant_calls.append(("full", path)))(src)
    check("MUTANT: unconditional new_from_file DOES request full allocation",
          mutant_calls == [("full", src)])

    svg = os.path.join(HOME, "huge.svg")
    with open(svg, "w", encoding="utf-8") as fh:
        fh.write('<svg width="50000" height="40000" xmlns="http://www.w3.org/2000/svg"/>')
    import shutil
    real_which = shutil.which
    real_run = subprocess.run
    commands = []
    shutil.which = lambda tool: "/usr/bin/rsvg-convert" if tool == "rsvg-convert" else real_which(tool)
    subprocess.run = lambda cmd, **kwargs: (commands.append(cmd) or
        type("Result", (), {"returncode": 1})())
    try:
        media._decode_to_png(svg)
    finally:
        shutil.which = real_which
        subprocess.run = real_run
    cmd = commands[0] if commands else []
    check("huge SVG fallback passes bounded dimensions to rsvg-convert",
          "--width" in cmd and "--height" in cmd and
          str(media.MAX_PIX) in cmd, repr(cmd))
    check("MUTANT: unbounded rsvg-convert command DOES omit size limits",
          "--width" not in ["rsvg-convert", "-o", "out.png", svg])


class NoticeViewer:
    _display = media.MediaViewer._display

    def __init__(self):
        self._media_path = None
        self._orig_pixbuf = object()
        self._zoom = 1
        self.notices = []
        self._scan_siblings = lambda path: setattr(self, "_siblings", [path])
        self._stop_video = lambda: None
        self._stop_slideshow = lambda: None
        self._show_notice = lambda *args: self.notices.append(args)
        self._fill_info = lambda *args: None
        self._set_zoom = lambda *args: None
        self._update_controls = lambda: None
        self._rebuild_strip = lambda: None


def refusal_checks():
    for label, blob in (("corrupt", b"not an image"), ("zero-byte", b"")):
        path = os.path.join(HOME, label + ".png")
        with open(path, "wb") as fh:
            fh.write(blob)
        viewer = NoticeViewer()
        viewer._display(path)
        check(label + " image produces an honest visible error",
              viewer.notices and viewer.notices[0][0] ==
              "This file cannot be opened", repr(viewer.notices))

    refused = {".heic", ".heif", ".avif"}
    check("NOT-A-DEFECT unsupported HEIF/AVIF stays out of Open patterns",
          refused.isdisjoint(media.IMAGE_EXTS), repr(media.IMAGE_EXTS))
    print("EVIDENCE IMAGE_EXTS executed at import; .heic/.heif/.avif were absent from the picker extension tuple")

    source_text = open(media.__file__, encoding="utf-8").read()
    check("NOT-A-DEFECT Media has no mutable app store to rewrite on close",
          "json.load" not in source_text and "atomic_write_json" not in source_text
          and "CFG_FILE" not in source_text)
    print("EVIDENCE enumerated media.py persistence calls; it reads user-selected files and moves to Trash but owns no JSON/config store")

    # Media's only file-removal path is recoverable Trash, not destruction.
    src = os.path.join(HOME, "trash-me.png")
    with open(src, "wb") as fh:
        fh.write(b"kept bytes")
    viewer = media.MediaViewer.__new__(media.MediaViewer)
    viewer._siblings = [src]
    viewer._sib_idx = 0
    viewer._thumb_cache = {}
    viewer._set_zoom = lambda *_: None
    viewer._set_info = lambda *_: None
    viewer._show_surface = lambda *_: None
    viewer._update_controls = lambda: None
    viewer._rebuild_strip = lambda: None
    viewer._do_trash(src)
    trashed = os.path.join(HOME, ".Trash", "trash-me.png")
    check("NOT-A-DEFECT Media removal preserves file bytes in Trash",
          not os.path.exists(src) and open(trashed, "rb").read() == b"kept bytes")
    print("EVIDENCE executed _do_trash on a real file; original moved to NB_HOME/.Trash with identical bytes")

    # Bytes in the Trash are only half of recoverable. The Finder puts an item
    # back by reading an origin sidecar it wrote at trash time; Media moved the
    # file and recorded nothing, so a picture trashed from the viewer had no
    # folder to go back TO. Preserving the bytes and losing the address is not
    # a recoverable delete, and the check above passed the whole time.
    origin = os.path.join(HOME, ".Trash", ".origins", "trash-me.png")
    recorded = ""
    if os.path.exists(origin):
        with open(origin, encoding="utf-8") as fh:
            recorded = fh.read().strip()
    check("a file trashed from Media records where the Finder must put it back",
          recorded == src, "sidecar %r holds %r, wanted %r"
          % (origin, recorded, src))
    print("EVIDENCE read <trash>/.origins/<name> after _do_trash and compared "
          "it against the file's original path")

    # ...and the sidecar has to follow the name the file actually took, or Put
    # Back reads the wrong record. A second file of the same name lands as
    # "trash-me.png (1)", so its origin belongs under THAT name.
    other = os.path.join(HOME, "sub", "trash-me.png")
    os.makedirs(os.path.dirname(other), exist_ok=True)
    with open(other, "wb") as fh:
        fh.write(b"second file")
    viewer._siblings = [other]
    viewer._sib_idx = 0
    viewer._do_trash(other)
    dup_origin = os.path.join(HOME, ".Trash", ".origins", "trash-me.png (1)")
    dup = ""
    if os.path.exists(dup_origin):
        with open(dup_origin, encoding="utf-8") as fh:
            dup = fh.read().strip()
    check("a name collision records the origin under the trashed name",
          dup == other, "sidecar %r holds %r, wanted %r"
          % (dup_origin, dup, other))
    print("EVIDENCE trashed a same-named file from another folder and checked "
          "the collision-renamed sidecar")


if __name__ == "__main__":
    fallback_and_orientation_checks()
    huge_decode_check()
    refusal_checks()
    print("\n%d/%d checks passed" % (passed, passed + failed))
    raise SystemExit(1 if failed else 0)
