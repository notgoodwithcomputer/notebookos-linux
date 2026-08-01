#!/usr/bin/env python3
"""Music must name tracks from the FILE'S OWN TAGS, and show its cover art.

The library used to be named from file paths alone: "01 Some Song.mp3" became
the title and the artist read "Unknown Artist", even when the file carried
perfectly good ID3 tags. The playbar's artwork was a placeholder icon that
nothing ever replaced. Both were reported as "metadata loads wrong, cover art
doesn't show".

What this protects, and why each check exists:

  * a tagged file shows ITS tags, not its filename       <- the reported bug
  * an UNTAGGED file still falls back to the filename    <- the fix must not
    convention ("Artist - Title.mp3")                       blank a library
  * a file with only SOME tags keeps the guess for the rest
  * the cover is extracted once and reloads from cache
  * the cache is keyed to the file, so replacing a file re-reads it

Run:  DISPLAY=:0 PYTHONPATH=<de dir> python3 tools/music_tags_selftest.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, DE)

RESULTS = []
FAILED = []


def check(name, ok, note=""):
    RESULTS.append(bool(ok))
    if not ok:
        FAILED.append(name)
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       "" if ok else "   <- %s" % (note,)))
    return bool(ok)


def have_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=10)
        return True
    except Exception:
        return False


def make_mp3(path, seconds=2, tags=None, cover=None):
    """A real MP3, optionally tagged and with attached cover art."""
    cmd = ["ffmpeg", "-f", "lavfi", "-i",
           "sine=frequency=440:duration=%d" % seconds]
    if cover:
        cmd += ["-i", cover, "-map", "0:a", "-map", "1:v",
                "-c:v", "copy", "-disposition:v", "attached_pic"]
    cmd += ["-map_metadata", "-1"]
    for k, v in (tags or {}).items():
        cmd += ["-metadata", "%s=%s" % (k, v)]
    cmd += ["-y", path]
    subprocess.run(cmd, capture_output=True, timeout=60)
    return os.path.isfile(path)


def make_cover(path):
    import cairo
    s = cairo.ImageSurface(cairo.FORMAT_RGB24, 240, 240)
    c = cairo.Context(s)
    c.set_source_rgb(0.79, 0.20, 0.12)
    c.paint()
    c.set_source_rgb(1, 1, 1)
    c.arc(120, 120, 70, 0, 6.2832)
    c.fill()
    s.write_to_png(path)
    return path


def main():
    if not have_ffmpeg():
        print("ffmpeg not available — cannot build test media")
        return 0

    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    src = tempfile.mkdtemp(prefix="mus_src_")
    cover = make_cover(os.path.join(src, "cover.png"))
    tagged = os.path.join(src, "tagged.mp3")
    plain = os.path.join(src, "plain.mp3")
    partial = os.path.join(src, "partial.mp3")
    make_mp3(tagged, tags={"title": "Tagged Song", "artist": "Some Artist",
                           "album": "An Album"}, cover=cover)
    make_mp3(plain)
    make_mp3(partial, tags={"album": "Only The Album"})

    home = tempfile.mkdtemp(prefix="mus_home_")
    os.environ["NB_HOME"] = home
    music_dir = os.path.join(home, "Music")
    os.makedirs(music_dir)
    os.makedirs(os.path.join(home, ".config", "notebook"))
    # the filename convention the old code relied on, so the fallback is real
    shutil.copy(tagged, os.path.join(music_dir, "01 whatever.mp3"))
    shutil.copy(plain, os.path.join(music_dir, "Nirvana - Lithium.mp3"))
    shutil.copy(partial, os.path.join(music_dir, "Beatles - Yesterday.mp3"))

    import inspect
    import music
    app = [c for _n, c in inspect.getmembers(music, inspect.isclass)
           if c.__module__ == "music" and issubclass(c, Gtk.Window)][0]

    if not music.GST_OK or music.GstPbutils is None:
        print("GStreamer/GstPbutils unavailable — tag reading cannot be tested")
        return 0

    win = app()

    def pump(seconds):
        end = time.time() + seconds
        while time.time() < end:
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
            time.sleep(0.05)

    pump(10)          # the discovery pass reads every file
    by = dict((os.path.basename(s["path"]), s) for s in win.songs)

    check("the library finds every audio file", len(win.songs) == 3, len(win.songs))

    t = by.get("01 whatever.mp3", {})
    check("a tagged file shows its own title, not its filename",
          t.get("title") == "Tagged Song", t.get("title"))
    check("  ... its own artist, not 'Unknown Artist'",
          t.get("artist") == "Some Artist", t.get("artist"))
    check("  ... and its own album", t.get("album") == "An Album", t.get("album"))
    check("  ... with a real duration", (t.get("time") or "") != "", t.get("time"))

    p = by.get("Nirvana - Lithium.mp3", {})
    check("an UNTAGGED file still reads from its filename",
          p.get("artist") == "Nirvana" and p.get("title") == "Lithium",
          (p.get("artist"), p.get("title")))

    q = by.get("Beatles - Yesterday.mp3", {})
    check("a half-tagged file takes the tag it has",
          q.get("album") == "Only The Album", q.get("album"))
    check("  ... and keeps the filename guess for the rest",
          q.get("artist") == "Beatles" and q.get("title") == "Yesterday",
          (q.get("artist"), q.get("title")))

    # ---- cover art ----
    cov = win._cover_file(t.get("path", ""))
    check("the embedded cover is extracted to the cache", os.path.isfile(cov))
    pb = win._cover_pixbuf(t.get("path", ""))
    check("  ... and loads as an image", pb is not None,
          None if pb is None else "%dx%d" % (pb.get_width(), pb.get_height()))
    win._play_track(t)
    pump(1)
    check("  ... and reaches the playbar's artwork",
          win._art_img is not None and win._art_img.get_pixbuf() is not None)
    nopb = win._cover_pixbuf(p.get("path", ""))
    check("a file with no cover yields no image (placeholder stays)",
          nopb is None)

    # ---- the cache is tied to the file, not just its name ----
    key_before = win._length_key(t.get("path", ""))
    time.sleep(1.1)
    shutil.copy(plain, t.get("path", ""))          # same name, different file
    key_after = win._length_key(t.get("path", ""))
    check("replacing a file invalidates its cached tags",
          key_before != key_after, (key_before, key_after))

    win.destroy()
    pump(0.5)

    print("\n%d/%d checks passed" % (sum(RESULTS), len(RESULTS)))
    if FAILED:
        print("\nFAILED:")
        for n in FAILED:
            print("  - " + n)
    shutil.rmtree(home, ignore_errors=True)
    shutil.rmtree(src, ignore_errors=True)
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
