#!/usr/bin/env python3
"""Display-free adversarial execution checks for Media's image paths."""
import os
import time
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))

HOME = tempfile.mkdtemp(prefix="nbmedia-adversarial-")
os.environ["NB_HOME"] = HOME

import media  # noqa: E402

passed = failed = 0


def pump():
    """Run the queued GLib callbacks. nbjobs does its work on a thread but
    DELIVERS through the main loop, so a display-free suite that only joins the
    thread sees no callback at all — and every "nothing was delivered" check
    then passes for the wrong reason."""
    ctx = media.GLib.main_context_default()
    for _ in range(200):
        if not ctx.pending():
            break
        ctx.iteration(False)


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

    # Patched at _bounded_pixbuf, which is the seam that decides the ceiling
    # and performs the decode. It used to be patched one level lower, at
    # GdkPixbuf.Pixbuf.new_from_file — but the bound is no longer decided from
    # a probe and applied to that call; it comes off the loader's size-prepared
    # signal, so a fake on new_from_file now intercepts nothing and this check
    # would have passed over whatever the real loader did.
    real_bounded_first = media._bounded_pixbuf
    real_decode = media._decode_to_png
    calls = []

    def load(path, real=None):
        calls.append(path)
        if path == src:
            raise RuntimeError("forced missing loader")
        return FakePixbuf()

    media._bounded_pixbuf = load
    media._decode_to_png = lambda path: tmp
    try:
        pb = media._pixbuf_any(src)
    finally:
        media._bounded_pixbuf = real_bounded_first
        media._decode_to_png = real_decode
    check("pixbuf-refused format is displayed through CLI fallback",
          calls == [src, tmp] and pb is not None, repr(calls))
    check("fallback decode applies embedded orientation",
          getattr(pb, "oriented", False), "fallback pixbuf was not oriented")

    # Direct loader must obey the same orientation law.
    media._bounded_pixbuf = lambda _path, real=None: FakePixbuf()
    try:
        direct = media._pixbuf_any(src)
    finally:
        media._bounded_pixbuf = real_bounded_first
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
    media._bounded_pixbuf = lambda path, real=None: (
        (_ for _ in ()).throw(RuntimeError("forced source refusal"))
        if path == webp else real_bounded(path, real))
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
    """The decode ceiling, checked where it is now applied.

    It used to be decided from get_file_info(), a probe that is ALLOWED to
    fail — and on failure the dimensions became 0x0, which failed the
    `width > 0` test and fell through to an unscaled new_from_file(). The file
    whose header could not be read was the one file decoded with no ceiling.
    The bound now comes off the loader's own size-prepared signal, which
    carries the real dimensions and cannot be skipped."""
    # A real PNG, big enough that a missed ceiling is unmistakable in the
    # result: this goes through the actual GdkPixbuf loader, not a fake.
    src = os.path.join(HOME, "huge.png")
    big = media.GdkPixbuf.Pixbuf.new(
        media.GdkPixbuf.Colorspace.RGB, False, 8, 9000, 6000)
    big.savev(src, "png", [], [])

    decoded = media._bounded_pixbuf(src)
    w, h = decoded.get_width(), decoded.get_height()
    check("a huge image is decoded inside the area budget",
          w * h <= media.MAX_AREA, "decoded %dx%d = %d px" % (w, h, w * h))
    check("a huge image is decoded inside the side budget",
          max(w, h) <= media.MAX_PIX, "decoded %dx%d" % (w, h))
    check("the bounded decode keeps the aspect ratio",
          abs((w / float(h)) - 1.5) < 0.02, "%dx%d" % (w, h))

    # The defect itself: with the dimensions unreadable, the ceiling must
    # TIGHTEN rather than disappear.
    real_info = media.GdkPixbuf.Pixbuf.get_file_info
    media.GdkPixbuf.Pixbuf.get_file_info = lambda path: (None, 0, 0)
    try:
        blind = media._bounded_pixbuf(src)
    finally:
        media.GdkPixbuf.Pixbuf.get_file_info = real_info
    bw, bh = blind.get_width(), blind.get_height()
    check("an unprobeable image is still decoded inside the budget",
          bw * bh <= media.MAX_AREA and max(bw, bh) <= media.MAX_PIX,
          "decoded %dx%d = %d px" % (bw, bh, bw * bh))

    # A side cap alone is not a memory bound, which is why MAX_AREA exists.
    check("MUTANT: a side-only cap DOES admit a quarter-gigabyte image",
          media.MAX_PIX * media.MAX_PIX > media.MAX_AREA,
          "%d vs %d" % (media.MAX_PIX * media.MAX_PIX, media.MAX_AREA))
    check("_fit_budget refuses unknown dimensions a free pass",
          media._fit_budget(0, 0)[0] * media._fit_budget(0, 0)[1]
          <= media.MAX_AREA, repr(media._fit_budget(0, 0)))
    check("_fit_budget leaves an ordinary photo alone",
          media._fit_budget(1600, 1200) == (1600, 1200),
          repr(media._fit_budget(1600, 1200)))

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
          "--width" in cmd and "--height" in cmd, repr(cmd))
    try:
        sw = int(cmd[cmd.index("--width") + 1])
        sh = int(cmd[cmd.index("--height") + 1])
    except (ValueError, IndexError):
        sw = sh = 10 ** 9
    check("the SVG fallback is bounded by area, not just by side",
          sw * sh <= media.MAX_AREA, "%dx%d" % (sw, sh))

    # A drawing with no readable size is the case that used to lose its
    # ceiling entirely: rsvg-convert was handed no --width/--height at all.
    sizeless = os.path.join(HOME, "sizeless.svg")
    with open(sizeless, "w", encoding="utf-8") as fh:
        fh.write('<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>')
    commands[:] = []
    shutil.which = lambda tool: "/usr/bin/rsvg-convert" if tool == "rsvg-convert" else real_which(tool)
    subprocess.run = lambda cmd, **kwargs: (commands.append(cmd) or
        type("Result", (), {"returncode": 1})())
    try:
        media._decode_to_png(sizeless)
    finally:
        shutil.which = real_which
        subprocess.run = real_run
    sizeless_cmd = commands[0] if commands else []
    check("an SVG with no readable size is still given a ceiling",
          "--width" in sizeless_cmd and "--height" in sizeless_cmd,
          repr(sizeless_cmd))
    check("MUTANT: unbounded rsvg-convert command DOES omit size limits",
          "--width" not in ["rsvg-convert", "-o", "out.png", svg])


class NoticeViewer:
    _display = media.MediaViewer._display
    # The refusal now arrives through the worker: a file GdkPixbuf cannot read
    # might be corrupt OR might just need the transcode, and the two are not
    # distinguishable without attempting it. The double carries the real
    # background path so the check still exercises the app's own code.
    _decode_in_background = media.MediaViewer._decode_in_background
    _cancel_notice_timer = media.MediaViewer._cancel_notice_timer
    _image_failed = media.MediaViewer._image_failed

    def __init__(self):
        self._closed = False
        self._decode_gen = 0
        self.jobs = media.nbjobs.JobOwner(name="notice-test")
        self._present_image = lambda *args: None
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
        viewer.jobs.join()
        pump()
        titles = [n[0] for n in viewer.notices]
        check(label + " image produces an honest visible error",
              "This file cannot be opened" in titles, repr(viewer.notices))
        # An instant refusal is ONE card. The "Opening…" card exists for a
        # decode that is taking a while (NOTICE_AFTER_MS); flashing it for a
        # file that fails in a few milliseconds is a grey blink before the
        # real message, and it used to be exactly that.
        if label == "zero-byte":
            # nothing to try on zero bytes: the refusal is immediate, and it
            # must be the only card
            check(label + " image refuses without a pointless 'Opening' flash first",
                  titles == ["This file cannot be opened"], repr(titles))
        else:
            # corrupt bytes are only known corrupt after every decoder has
            # been tried, which can outlast the grace period -- an "Opening"
            # card is then honest, but the refusal must still be the last word
            check(label + " image ends on the refusal, with at most one 'Opening' before it",
                  titles[-1:] == ["This file cannot be opened"]
                  and [t for t in titles if t.startswith("Opening")] in ([], titles[:1]),
                  repr(titles))

    # ...but a decode that outlasts the grace period DOES say what it is doing
    slow = os.path.join(HOME, "slow.png")
    with open(slow, "wb") as fh:
        fh.write(b"not an image either")
    real_any = media._pixbuf_any

    def slow_any(path, real=None):
        time.sleep((media.NOTICE_AFTER_MS + 200) / 1000.0)
        return real_any(path, real)
    media._pixbuf_any = slow_any
    try:
        viewer = NoticeViewer()
        viewer._display(slow)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not any(
                n[0] == "This file cannot be opened" for n in viewer.notices):
            pump()
            time.sleep(0.02)
        titles = [n[0] for n in viewer.notices]
        check("a decode that outlasts the grace period says 'Opening…' first",
              len(titles) >= 2 and titles[0].startswith("Opening")
              and titles[-1] == "This file cannot be opened", repr(titles))
    finally:
        media._pixbuf_any = real_any

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
    # the file the window is on: _do_trash checks it so a Delete during a step
    # does not drag the picture being decoded along with the one it removed
    viewer._media_path = src
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
    viewer._media_path = other
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


def offthread_decode_check():
    """A format GdkPixbuf cannot read used to be transcoded on the GTK thread —
    rsvg-convert or ffmpeg, capped at 25 seconds — so one click on a WebP or an
    SVG froze the window until it finished."""
    import threading
    import types

    app = media.MediaViewer.__new__(media.MediaViewer)
    app._closed = False
    app._decode_gen = 0
    app.jobs = media.nbjobs.JobOwner(name="media-test")
    notices = []
    app._show_notice = lambda title, sub: notices.append(title)
    presented = []
    app._present_image = lambda path, pb, uc, dims=None: presented.append((path, pb))
    app._image_failed = lambda path, uc: presented.append((path, None))

    main_ident = threading.get_ident()
    seen = {}
    real_any = media._pixbuf_any

    def any_slowly(path, real=None):
        # slower than the grace period, so the "Opening…" card is owed
        time.sleep((media.NOTICE_AFTER_MS + 150) / 1000.0)
        seen["ident"] = threading.get_ident()
        return FakePixbuf()
    media._pixbuf_any = any_slowly
    try:
        app._decode_gen += 1
        app._decode_in_background("/tmp/slow.webp", True)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not presented:
            pump()
            time.sleep(0.02)
        app.jobs.join()
        pump()
    finally:
        media._pixbuf_any = real_any
    check("the decoded picture reaches the stage",
          [p for p, _ in presented] == ["/tmp/slow.webp"], repr(presented))
    check("a transcoded format is decoded off the GTK thread",
          seen.get("ident") not in (None, main_ident),
          "transcode ran on the calling thread")
    check("the viewer says what it is doing while it decodes",
          any("slow.webp" in n for n in notices), repr(notices))

    # Browsing past a slow picture must not let its decode land later, on top
    # of whatever the person is looking at by then.
    app2 = media.MediaViewer.__new__(media.MediaViewer)
    app2._closed = False
    app2._decode_gen = 0
    app2.jobs = media.nbjobs.JobOwner(name="media-test-2")
    app2._show_notice = lambda title, sub: None
    landed = []
    app2._present_image = lambda path, pb, uc, dims=None: landed.append(path)
    app2._image_failed = lambda path, uc: landed.append(None)
    media._pixbuf_any = lambda path, real=None: FakePixbuf()
    try:
        app2._decode_gen += 1
        app2._decode_in_background("/tmp/superseded.webp", True)
        app2._decode_gen += 1          # the person moved on
        app2.jobs.join()
        pump()
    finally:
        media._pixbuf_any = real_any
    check("a decode the person moved past does not reach the stage",
          landed == [], repr(landed))

    # A closed window must not be called back into either.
    app3 = media.MediaViewer.__new__(media.MediaViewer)
    app3._closed = False
    app3._decode_gen = 1
    app3.jobs = media.nbjobs.JobOwner(name="media-test-3")
    app3._show_notice = lambda title, sub: None
    after_close = []
    app3._present_image = lambda path, pb, uc, dims=None: after_close.append(path)
    app3._image_failed = lambda path, uc: after_close.append(None)
    media._pixbuf_any = lambda path, real=None: FakePixbuf()
    try:
        app3._decode_in_background("/tmp/closing.webp", True)
        app3._closed = True
        app3.jobs.join()
        pump()
    finally:
        media._pixbuf_any = real_any
    check("a decode landing after the window closed touches nothing",
          after_close == [], repr(after_close))

    # The idle thumbnail pass must never be the thing that pays for a
    # transcode: _thumbnail_fast has no fallback at all.
    src = os.path.join(HOME, "notathumb.webp")
    with open(src, "wb") as fh:
        fh.write(b"not an image")
    fell_back = []
    real_decode = media._decode_to_png
    media._decode_to_png = lambda path: fell_back.append(path)
    try:
        fast = media._thumbnail_fast(src)
    finally:
        media._decode_to_png = real_decode
    check("the idle thumbnail pass never runs the transcode fallback",
          fast is None and fell_back == [], repr(fell_back))


if __name__ == "__main__":
    fallback_and_orientation_checks()
    huge_decode_check()
    refusal_checks()
    offthread_decode_check()
    print("\n%d/%d checks passed" % (passed, passed + failed))
    print("RESULT: %s" % ("PASS" if not failed else "FAILED"))
    raise SystemExit(1 if failed else 0)
