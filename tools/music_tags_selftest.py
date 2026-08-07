#!/usr/bin/env python3
"""
The library shows what the file says, not what it is called.

ROADMAP #28: *"ID3 tags are never read although the tag reader is already
running. The whole Albums/Artists sidebar is filename guesses."* Measured today
that is no longer true — `_info_tags` pulls title/artist/album off the
`DiscovererInfo` that already runs over every file for its duration — and this
suite is what establishes it, and keeps it true.

The test file is built to make the two answers DIFFERENT. It is named
`01 track.mp3`, so filename-derived metadata gives "01 track" /
"Unknown Artist" / "Unknown Album", while the tags inside say
"Blue Monday" / "New Order" / "Power Corruption and Lies". A suite whose
fixture was called "New Order - Blue Monday.mp3" would pass whether or not a
single tag was read.

Building the fixture
--------------------
The audio is encoded by GStreamer, which the app requires anyway, and the ID3v2
tag is written BY HAND. Both `taginject`+`id3v2mux` and `taginject`+`id3mux`
produced files whose tags read back empty, and no external tagger is assumed to
exist on the guest — `ffmpeg` is not in the image. A hand-built ID3v2.3 header
is 25 lines, has no dependencies, and is exact about what is being tested.

Run:
    tools/guestrun.sh python3 tools/music_tags_selftest.py
    tools/guestrun.sh python3 tools/music_tags_selftest.py --de DIR
"""
import os
import sys
import time
import struct
import shutil
import tempfile

_HOME = tempfile.mkdtemp(prefix="nb-tags-")
os.environ["NB_HOME"] = _HOME
MUSIC = os.path.join(_HOME, "Music")
os.makedirs(MUSIC, exist_ok=True)

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.join(os.path.dirname(HERE), "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
if "--de" in sys.argv:
    DE = os.path.abspath(sys.argv[sys.argv.index("--de") + 1])
sys.path.insert(0, DE)

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gst", "1.0")
gi.require_version("GstPbutils", "1.0")
from gi.repository import Gtk, Gst  # noqa: E402

import music  # noqa: E402

TITLE, ARTIST, ALBUM = "Blue Monday", "New Order", "Power Corruption and Lies"
FILENAME = "01 track.mp3"          # deliberately says nothing useful

FAILED, N = [], [0]


def check(name, cond, detail=""):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name + (("  " + detail) if detail else ""))
    if not cond:
        FAILED.append(name)
    return bool(cond)


def not_reached(reason, *names):
    for n in names:
        check("%s  [not reached: %s]" % (n, reason), False)


def pump(n=400):
    i = 0
    while Gtk.events_pending() and i < n:
        Gtk.main_iteration()
        i += 1


def _id3v2(title, artist, album):
    """A minimal, valid ID3v2.3 tag. Sizes are plain big-endian inside a frame
    and SYNCSAFE (7 bits per byte) in the header — getting that backwards
    produces a tag every reader silently ignores, which would look exactly like
    the bug under test."""
    def frame(fid, text):
        data = b"\x00" + text.encode("latin-1")     # 0x00 = ISO-8859-1
        return fid + struct.pack(">I", len(data)) + b"\x00\x00" + data

    body = (frame(b"TIT2", title) + frame(b"TPE1", artist)
            + frame(b"TALB", album))
    n = len(body)
    syncsafe = bytes([(n >> 21) & 0x7f, (n >> 14) & 0x7f,
                      (n >> 7) & 0x7f, n & 0x7f])
    return b"ID3" + b"\x03\x00" + b"\x00" + syncsafe + body


def build_fixture(path):
    Gst.init(None)
    raw = path + ".raw"
    pipe = Gst.parse_launch(
        'audiotestsrc num-buffers=90 ! audioconvert ! lamemp3enc '
        '! filesink location="%s"' % raw)
    pipe.set_state(Gst.State.PLAYING)
    pipe.get_bus().timed_pop_filtered(
        20 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
    pipe.set_state(Gst.State.NULL)
    if not os.path.exists(raw) or os.path.getsize(raw) < 512:
        return False
    with open(path, "wb") as out:
        out.write(_id3v2(TITLE, ARTIST, ALBUM))
        with open(raw, "rb") as fh:
            out.write(fh.read())
    os.remove(raw)
    return True


def main():
    path = os.path.join(MUSIC, FILENAME)
    made = check("a tagged MP3 fixture can be built", build_fixture(path))
    if not made:
        not_reached("no fixture to read",
                    "the scan starts from the filename",
                    "the tags replace the filename guess",
                    "the duration arrives too")
        return 1

    app = music.Music()
    pump()
    check("the library finds the file", len(app.songs) == 1,
          "%d track(s)" % len(app.songs))
    if not app.songs:
        not_reached("nothing was scanned",
                    "the scan starts from the filename",
                    "the tags replace the filename guess",
                    "the duration arrives too")
        return 1

    # The filename guess is the BEFORE state, and asserting it is what makes
    # the after state meaningful: it proves the two differ, so the later values
    # can only have come from the tags.
    s = app.songs[0]
    check("the scan starts from the filename",
          s.get("title") == "01 track" and s.get("artist") == "Unknown Artist",
          "%r / %r" % (s.get("title"), s.get("artist")))

    # The Discoverer runs on its own thread and hands results back on the main
    # loop, so this waits for it rather than assuming a fixed delay.
    end = time.time() + 20
    while time.time() < end:
        pump()
        if app.songs and app.songs[0].get("title") == TITLE:
            break
        time.sleep(0.1)
    s = app.songs[0]

    got = check("the tags replace the filename guess",
                s.get("title") == TITLE and s.get("artist") == ARTIST
                and s.get("album") == ALBUM,
                "%r / %r / %r" % (s.get("title"), s.get("artist"),
                                  s.get("album")))
    # Duration is asserted UNCONDITIONALLY, not behind `got`. It rides the same
    # Discoverer pass but a different accessor, so it survives the tags being
    # discarded — gating it on the tag check produced a "[not reached]" that
    # named the wrong cause, which is worse than no diagnostic.
    check("the duration arrives too",
          bool(s.get("time")) and s.get("time") != "0:00",
          "time=%r" % s.get("time"))

    try:
        app.destroy()
    except Exception:
        pass

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
    shutil.rmtree(_HOME, ignore_errors=True)
sys.exit(rc)
