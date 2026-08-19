#!/usr/bin/env python3
"""burner_selftest — would the Disc Burner actually produce a playable disc?

    DISPLAY=:0 python3 tools/burner_selftest.py

Nothing here burns anything: there is no drive on a build machine and no blank
media in a test. What CAN be checked is everything up to the moment the laser
turns on, and that is where the defects live — a disc that turns out to be
unplayable was already wrong long before it was written.

So this proves, against the real functions rather than a description of them:

1. **The capacity arithmetic**, which is what stands between a person and
   twenty minutes of encoding that ends in a full disc.
2. **The menu geometry**, and specifically that the rectangle the text is drawn
   in IS the rectangle the remote highlights. Those are produced for two
   different tools — cairo and spumux — and if they ever drift, the disc looks
   right and navigates wrong.
3. **The menu really is white text on black**, read back off the rendered
   surface pixel by pixel, because that is what was asked for and a CSS-less
   cairo drawing has no other way to be checked.
4. **The authoring XML**, including the one form of jump that DVD-Video allows
   from a menu. `jump titleset 1 title 1` is rejected by dvdauthor after it has
   already written a partial VIDEO_TS, which looks like success.
5. **The commands**, each of which carries at least one flag that is not
   optional: -dao (no gaps between songs), -udf (a DVD player can read it),
   -ac 2 (a mono menu is out of spec), -dvd-compat (closed disc).

Every family ends with a MUTANT: the check is re-run against a deliberately
broken version and must go RED. A gate that cannot go red is not a gate.

Exit status is the number of failures.
"""
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO,
                  "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, DE)
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="burner-selftest-"))

import cairo                                                  # noqa: E402
import burner                                                 # noqa: E402

FAILS = []
CHECKS = [0]


def check(name, ok, detail=""):
    CHECKS[0] += 1
    print("%-4s %s%s" % ("ok" if ok else "FAIL", name,
                         "" if ok else "   -> " + str(detail)))
    if not ok:
        FAILS.append(name)


def mutant(name, ok_when_broken):
    CHECKS[0] += 1
    caught = not ok_when_broken
    print("%-4s MUTANT %s%s" % ("ok" if caught else "FAIL", name,
                                "" if caught else
                                "   -> sabotage went UNDETECTED"))
    if not caught:
        FAILS.append("MUTANT " + name)


# ===================================================== 1. capacity arithmetic
print("--- 1. what fits on the disc ------------------------------------")

class _GoProbe:
    busy = False
    items = [{"path": "/tmp/song.wav"}]
    drive = {"node": "/dev/sr0"}
    mode = burner.DiscBurner.AUDIO
    disc = {"present": False}
    confirmed = False
    message = ""
    def _say(self, text): self.message = text
    def _confirm(self): self.confirmed = True

_go_probe = _GoProbe()
burner.DiscBurner._on_go(_go_probe, None)
check("an empty drive cannot reach burn confirmation",
      not _go_probe.confirmed and bool(_go_probe.message))
_go_probe.disc = {}
_go_probe.message = ""
burner.DiscBurner._on_go(_go_probe, None)
check("a drive still being probed cannot reach burn confirmation",
      not _go_probe.confirmed and bool(_go_probe.message))

class _MoveProbe:
    busy = False
    items = [{"name": "One"}, {"name": "Two"}, {"name": "Three"}]
    _sel = 1
    refreshed = 0
    def _refresh(self): self.refreshed += 1

_move_probe = _MoveProbe()
burner.DiscBurner._on_move(_move_probe, None, -1)
check("moving keeps the same item selected at its new position",
      [x["name"] for x in _move_probe.items] == ["Two", "One", "Three"]
      and _move_probe._sel == 0 and _move_probe.refreshed == 1)
_burner_source = open(burner.__file__, encoding="utf-8").read()
check("the selected burn row exposes toggle state and retained focus",
      "btn = Gtk.ToggleButton()" in _burner_source
      and "btn.set_active(i == getattr(self, \"_sel\", None))" in _burner_source
      and "chosen.grab_focus()" in _burner_source)

M = 60.0
check("an 80-minute CD takes a 79-minute programme",
      burner.cd_fits([40 * M, 39 * M])[1])
check("...and refuses an 81-minute one",
      not burner.cd_fits([41 * M, 40 * M])[1])
check("the CD limit is under a nominal 80:00, not at it",
      burner.CD_MAX_SECONDS < 80 * 60, burner.CD_MAX_SECONDS)
check("an empty disc fits", burner.cd_fits([])[1])
check("an unreadable track (None) does not poison the total",
      burner.cd_fits([60.0, None, 60.0])[0] == 120.0,
      burner.cd_fits([60.0, None, 60.0])[0])

# The bitrate has to come DOWN as the programme gets longer, or a long film
# overruns the disc at the very end — after the encode, which is the expensive
# way to discover it.
short = burner.dvd_video_bitrate(20 * M)
feature = burner.dvd_video_bitrate(120 * M)
epic = burner.dvd_video_bitrate(600 * M)
check("a short film gets a high bitrate", short == burner.DVD_PEAK_KBIT, short)
check("a feature gets less than a short film", feature < short,
      (feature, short))
check("bitrate never falls below the watchable floor",
      epic >= burner.DVD_MIN_KBIT, epic)
check("bitrate never exceeds the DVD-Video ceiling",
      all(burner.dvd_video_bitrate(t) <= burner.DVD_PEAK_KBIT
          for t in (1, 60, 3600, 36000)))
check("a two-hour film fits one disc", burner.dvd_fits(120 * M))
check("ten hours does not fit at a watchable quality",
      not burner.dvd_fits(600 * M))


def bitrate_holds(fn):
    # STRICTLY decreasing, not merely non-increasing. Written with >= first
    # time round, and a constant-bitrate mutant sailed through it: every
    # comparison is satisfied by equality, so the check could not tell a
    # bitrate that responds to the running time from one that ignores it.
    return (fn(20 * M) > fn(120 * M) > fn(600 * M)
            and fn(600 * M) >= burner.DVD_MIN_KBIT
            and fn(20 * M) <= burner.DVD_PEAK_KBIT)


check("bitrate falls monotonically with length", bitrate_holds(
    burner.dvd_video_bitrate))
mutant("a constant bitrate that ignores the running time",
       bitrate_holds(lambda t, u=None: burner.DVD_PEAK_KBIT))
mutant("a bitrate with no floor",
       bitrate_holds(lambda t, u=None: max(1, int(1000000.0 / max(1, t)))))

# The size that actually matters is the one a person's disc is sold as.
check("a single-layer DVD is the real 4.7 GB, not 4.7 GiB",
      burner.DVD_BYTES == 4700372992, burner.DVD_BYTES)
check("scratch room asked for is bigger than the finished disc",
      burner.scratch_needed(90 * M) > burner.DVD_USABLE,
      burner.scratch_needed(90 * M))


# ======================================================== 2. menu geometry
print("\n--- 2. the menu's buttons sit on the menu's rows ------------------")

TITLES = ["Holiday", "The Garden", "Birthday"]
lay = burner.menu_layout(TITLES, burner.NTSC)
check("NTSC menus are a 720x480 frame",
      (lay["width"], lay["height"]) == (720, 480),
      (lay["width"], lay["height"]))
pal = burner.menu_layout(TITLES, burner.PAL)
check("PAL menus are a 720x576 frame",
      (pal["width"], pal["height"]) == (720, 576),
      (pal["width"], pal["height"]))
check("a row per title", len(lay["rows"]) == 3, len(lay["rows"]))

# THE check this section exists for: two different tools are told where things
# are, and they must be told the same thing.
inside = []
for row in lay["rows"]:
    x0, y0, x1, y1 = row["button"]
    inside.append(y0 <= row["top"] and row["baseline"] <= y1
                  and x0 <= lay["text_x"] < x1)
check("every button rectangle contains the row's text and baseline",
      all(inside), inside)

overlap = []
for a, b in zip(lay["rows"], lay["rows"][1:]):
    overlap.append(a["button"][3] <= b["button"][1])
check("no two button rectangles overlap", all(overlap), overlap)

safe = []
for row in lay["rows"]:
    x0, y0, x1, y1 = row["button"]
    safe.append(x0 >= 0 and y0 >= 0 and x1 <= lay["width"]
                and y1 <= lay["height"])
check("every button is inside the frame", all(safe), safe)
check("the text is inset from the frame edge for television overscan",
      lay["inset_x"] >= 0.08 * lay["width"], lay["inset_x"])
check("a menu holds at most the titles a remote can reach",
      len(burner.menu_layout(["x"] * 40, burner.NTSC)["rows"])
      == burner.MAX_TITLES)


def geometry_agrees(layout):
    for row in layout["rows"]:
        x0, y0, x1, y1 = row["button"]
        if not (y0 <= row["top"] and row["baseline"] <= y1):
            return False
    return True


broken = burner.menu_layout(TITLES, burner.NTSC)
for r in broken["rows"]:                    # buttons drift off their rows
    x0, y0, x1, y1 = r["button"]
    r["button"] = (x0, y0 + 200, x1, y1 + 200)
mutant("button rectangles that have drifted off their text",
       geometry_agrees(broken))


# =================================================== 3. white text on black
print("\n--- 3. the menu is white text on black ---------------------------")

tmp = tempfile.mkdtemp(prefix="burner-menu-")
png = os.path.join(tmp, "menu.png")
burner.render_menu_png(png, "My Disc", TITLES, burner.NTSC)
surf = cairo.ImageSurface.create_from_png(png)
check("the menu renders at the frame size",
      (surf.get_width(), surf.get_height()) == (720, 480),
      (surf.get_width(), surf.get_height()))

data = surf.get_data()
stride = surf.get_stride()


def pixel(x, y):
    i = y * stride + x * 4
    return data[i + 2], data[i + 1], data[i]        # BGRA -> r, g, b


w, h = surf.get_width(), surf.get_height()
tones = {}
for y in range(0, h, 2):
    for x in range(0, w, 2):
        r, g, b = pixel(x, y)
        tones[(r, g, b)] = tones.get((r, g, b), 0) + 1
black = sum(n for (r, g, b), n in tones.items() if r < 24 and g < 24 and b < 24)
light = sum(n for (r, g, b), n in tones.items()
            if r > 200 and g > 200 and b > 200)
sampled = sum(tones.values())
check("the background is black", black / float(sampled) > 0.90,
      "%.3f black" % (black / float(sampled)))
check("there is white text on it", light > 0, light)
check("every pixel is a neutral grey — no colour anywhere",
      all(abs(r - g) <= 2 and abs(g - b) <= 2 for (r, g, b) in tones),
      [t for t in tones if abs(t[0] - t[1]) > 2][:4])
check("the corners are black (no border, no panel, no gradient)",
      all(pixel(x, y)[0] < 24 for x, y in
          ((1, 1), (w - 2, 1), (1, h - 2), (w - 2, h - 2))))

# Ink has to land ON the rows, not merely somewhere in the frame.
row_ink = []
for row in lay["rows"]:
    x0, y0, x1, y1 = row["button"]
    found = any(pixel(x, y)[0] > 200
                for y in range(max(0, y0), min(h, y1), 2)
                for x in range(max(0, x0), min(w, x1), 3))
    row_ink.append(found)
check("every row has text drawn inside its own button", all(row_ink), row_ink)

# The disc name is a field a person types, on an OS that speaks seventeen
# languages. Drawn through cairo's toy font API this came out as empty boxes
# for every non-Latin script — the toy API takes one family and does no
# fallback and no shaping. Pango picks a face per run instead.
def heading_ink(title):
    p = os.path.join(tmp, "script.png")
    lay2 = burner.render_menu_png(p, title, ["A", "B"], burner.NTSC)
    s = cairo.ImageSurface.create_from_png(p)
    d, st = s.get_data(), s.get_stride()
    return sum(1 for y in range(lay2["inset_y"], lay2["heading_y"] + 8)
               for x in range(lay2["inset_x"], lay2["width"] - 40, 2)
               if d[y * st + x * 4] > 200)


for _label, _title in (("Japanese", "休暇の記録"), ("Hindi", "छुट्टी"),
                       ("Russian", "Отпуск"), ("Yiddish", "וואַקאַציע"),
                       ("Chinese", "假期录像")):
    check("a disc named in %s draws real glyphs, not boxes" % _label,
          heading_ink(_title) > 40, heading_ink(_title))

hl = os.path.join(tmp, "highlight.png")
burner.render_highlight_png(hl, lay)
hsurf = cairo.ImageSurface.create_from_png(hl)
hdata, hstride = hsurf.get_data(), hsurf.get_stride()
check("the highlight layer matches the frame size",
      (hsurf.get_width(), hsurf.get_height()) == (720, 480))
alphas = set()
for y in range(0, hsurf.get_height(), 2):
    for x in range(0, hsurf.get_width(), 2):
        alphas.add(hdata[y * hstride + x * 4 + 3])
# A subpicture is two bits deep. Anti-aliased edges quantise to noise, so the
# layer has to be hard-edged: transparent or opaque, nothing between.
check("the highlight layer is hard-edged (a subpicture is 2-bit)",
      alphas <= {0, 255}, sorted(alphas)[:6])


def is_white_on_black(path):
    s = cairo.ImageSurface.create_from_png(path)
    d, st = s.get_data(), s.get_stride()
    dark = 0
    total = 0
    for yy in range(0, s.get_height(), 4):
        for xx in range(0, s.get_width(), 4):
            i = yy * st + xx * 4
            total += 1
            if d[i] < 24 and d[i + 1] < 24 and d[i + 2] < 24:
                dark += 1
    return dark / float(total) > 0.90


inverted = os.path.join(tmp, "inverted.png")
s2 = cairo.ImageSurface(cairo.FORMAT_RGB24, 720, 480)
c2 = cairo.Context(s2)
c2.set_source_rgb(1, 1, 1)          # a white background: the thing NOT wanted
c2.paint()
s2.write_to_png(inverted)
mutant("a menu drawn on a white background", is_white_on_black(inverted))


# ====================================================== 4. the authoring XML
print("\n--- 4. the disc's navigation -------------------------------------")

xml = burner.dvdauthor_xml("/out", "menu_sub.mpg",
                           ["t1.mpg", "t2.mpg", "t3.mpg"], burner.NTSC)
check("the menu is the disc's entry point", 'entry="title"' in xml)
check("the menu waits rather than timing out", 'pause="inf"' in xml)
check("one button per title",
      all(('name="t%d"' % n) in xml for n in (1, 2, 3)))
check("buttons use the ONLY jump form a VMGM menu allows",
      "jump title 1;" in xml and "jump titleset" not in xml)
check("each title returns to the menu when it ends",
      xml.count("call vmgm menu 1;") == 3, xml.count("call vmgm menu 1;"))
check("the video standard reaches both the menu and the titles",
      xml.count('format="ntsc"') == 2, xml.count('format="ntsc"'))
pal_xml = burner.dvdauthor_xml("/out", "m.mpg", ["a.mpg"], burner.PAL)
check("a PAL disc says pal, not ntsc",
      'format="pal"' in pal_xml and "ntsc" not in pal_xml)

spu = burner.spumux_xml(lay)
check("the subpicture names the same buttons the XML jumps to",
      all(('name="t%d"' % n) in spu for n in (1, 2, 3)))
coords_agree = all(
    ('x0="%d" y0="%d" x1="%d" y1="%d"' % row["button"]) in spu
    for row in lay["rows"])
check("the subpicture's rectangles ARE the layout's rectangles", coords_agree)


def jump_is_legal(text):
    return "jump title 1;" in text and "jump titleset" not in text


mutant("the jump form dvdauthor rejects from a menu",
       jump_is_legal(xml.replace("jump title 1;", "jump titleset 1 title 1;")))
mutant("titles that never return to the menu",
       burner.dvdauthor_xml("/o", "m", ["a"], burner.NTSC)
       .replace("call vmgm menu 1;", "").count("call vmgm menu 1;") == 1)


# =========================================================== 5. the commands
print("\n--- 5. the flags that are not optional ---------------------------")

cd = burner.audio_burn_cmd("/dev/sr0", ["a.wav", "b.wav"])
check("an audio CD is written disc-at-once, so songs have no gap between",
      "-dao" in cd)
check("audio tracks are padded to a whole sector", "-pad" in cd)
check("the disc is written as audio, not as data", "-audio" in cd)
check("the drive is named", "dev=/dev/sr0" in cd)
check("every track reaches the command", cd[-2:] == ["a.wav", "b.wav"])

dec = burner.decode_track_cmd("song.flac", "t1.wav")
check("decoding targets Red Book: 44100 Hz, stereo, 16-bit",
      "44100" in dec and dec[dec.index("-ac") + 1] == "2"
      and "pcm_s16le" in dec)
check("cover art is dropped, or the WAV is unreadable", "-vn" in dec)

tr = burner.transcode_title_cmd("in.mkv", "out.mpg", burner.NTSC, 4000)
check("a title is encoded to the DVD target", "ntsc-dvd" in tr)
check("a mono source is forced to stereo", tr[tr.index("-ac") + 1] == "2")
check("the solved bitrate reaches the encoder", "4000k" in tr)
pal_tr = burner.transcode_title_cmd("in.mkv", "out.mpg", burner.PAL, None)
check("a PAL title uses the PAL target", "pal-dvd" in pal_tr)

menu = burner.menu_video_cmd("menu.png", "menu.mpg", burner.NTSC)
check("the menu still is held for a few seconds",
      "-t" in menu and float(menu[menu.index("-t") + 1]) > 0)
check("the menu carries a real silent stereo track",
      any("anullsrc" in a and "stereo" in a for a in menu))
check("the menu is 4:3 so it fills a television",
      menu[menu.index("-aspect") + 1] == "4:3")

iso = burner.iso_cmd("/dvd", "/disc.iso", "MY_DISC")
check("the image is laid out as DVD-Video", "-dvd-video" in iso)
check("the image carries UDF, which is what a DVD player reads", "-udf" in iso)

burn = burner.dvd_burn_cmd("/dev/sr0", "/disc.iso")
check("the DVD is closed so a player will read it", "-dvd-compat" in burn)
check("the image is written to the drive", "/dev/sr0=/disc.iso" in burn)

check("a volume label is legal: upper case, no spaces, 32 max",
      burner.disc_label("My Holiday: 2026!") == "MY_HOLIDAY_2026",
      burner.disc_label("My Holiday: 2026!"))
check("an empty name still yields a legal label",
      burner.disc_label("") == "DISC" and burner.disc_label(None) == "DISC")
check("a very long name is cut to the limit",
      len(burner.disc_label("x" * 90)) == 32)


def audio_has_gapless(cmd):
    return "-dao" in cmd and "-audio" in cmd


mutant("an audio burn without disc-at-once (a gap between every song)",
       audio_has_gapless([c for c in cd if c != "-dao"]))
mutant("an image with no UDF for the player to read",
       "-udf" in [c for c in iso if c != "-udf"])


# ======================================= 6. the real tools accept all of it
print("\n--- 6. the tools that ship accept what the app generates ----------")

# The checks above read the app's own output. This one hands that output to the
# actual dvdauthor / spumux / genisoimage that ship in the image and keeps what
# comes back — the difference between "the XML contains the right words" and
# "the disc builds". Every argument here comes from burner's own functions.
#
# The target binaries are cross-built for the guest but this is the same
# architecture, so they run through their own loader. When output/target has
# not been built there is nothing to test against and this SKIPS VISIBLY: a
# section that quietly evaporates reports the same green as one that passed.
TARGET = os.path.join(REPO, "buildroot/output/target")
LOADER = os.path.join(TARGET, "lib/ld-linux-x86-64.so.2")
NEEDED = ("ffmpeg", "spumux", "dvdauthor", "genisoimage")
missing = [b for b in NEEDED
           if not os.path.exists(os.path.join(TARGET, "usr/bin", b))]

if not os.path.exists(LOADER) or missing:
    why = ("output/target not built" if not os.path.exists(LOADER)
           else "not built: " + ", ".join(missing))
    print("SKIP the shipped tools accept the app's output   -> %s" % why)
    SKIPPED = True
else:
    import shutil as _shutil
    import subprocess

    SKIPPED = False
    work = tempfile.mkdtemp(prefix="burner-e2e-")
    libs = os.path.join(TARGET, "usr/lib") + ":" + os.path.join(TARGET, "lib")

    def guest(cmd, **kw):
        """Run one of the app's OWN command lists against the shipped tool."""
        full = [LOADER, "--library-path", libs,
                os.path.join(TARGET, "usr/bin", cmd[0])] + list(cmd[1:])
        return subprocess.run(full, capture_output=True, text=True,
                              cwd=work, timeout=600, **kw)

    names = ["Holiday", "The Garden"]
    layout = burner.render_menu_png(os.path.join(work, "menu.png"),
                                    "My Disc", names, burner.NTSC)
    burner.render_highlight_png(os.path.join(work, "highlight.png"), layout)
    with open(os.path.join(work, "spumux.xml"), "w", encoding="utf-8") as fh:
        fh.write(burner.spumux_xml(layout))

    r = guest(burner.menu_video_cmd("menu.png", "menu.mpg", burner.NTSC))
    check("ffmpeg builds the menu still from the app's own command",
          r.returncode == 0, r.stdout[-300:])

    guest(["ffmpeg", "-nostdin", "-y", "-f", "lavfi", "-i",
           "testsrc=size=320x240:rate=15:duration=1", "-f", "lavfi",
           "-i", "sine=duration=1", "-c:v", "mpeg4", "src.mp4"])
    r = guest(burner.transcode_title_cmd("src.mp4", "title1.mpg",
                                         burner.NTSC, 3000))
    check("ffmpeg accepts the app's DVD transcode command",
          r.returncode == 0, r.stdout[-300:])

    ok = False
    err = ""
    if os.path.exists(os.path.join(work, "menu.mpg")):
        with open(os.path.join(work, "menu.mpg"), "rb") as src, \
                open(os.path.join(work, "menu_sub.mpg"), "wb") as dst:
            p = subprocess.run(
                [LOADER, "--library-path", libs,
                 os.path.join(TARGET, "usr/bin/spumux"), "spumux.xml"],
                stdin=src, stdout=dst, stderr=subprocess.PIPE, text=True,
                cwd=work, timeout=600)
        ok = p.returncode == 0 and os.path.getsize(
            os.path.join(work, "menu_sub.mpg")) > 0
        err = (p.stderr or "")[-300:]
    check("spumux accepts the app's subpicture XML and its button rectangles",
          ok, err)

    if os.path.exists(os.path.join(work, "title1.mpg")):
        _shutil.copy(os.path.join(work, "title1.mpg"),
                     os.path.join(work, "title2.mpg"))
    with open(os.path.join(work, "dvdauthor.xml"), "w",
              encoding="utf-8") as fh:
        fh.write(burner.dvdauthor_xml(os.path.join(work, "DVD"),
                                      "menu_sub.mpg",
                                      ["title1.mpg", "title2.mpg"],
                                      burner.NTSC))
    r = guest(["dvdauthor", "-x", "dvdauthor.xml"])
    errs = [ln for ln in (r.stderr or "").splitlines()
            if ln.startswith("ERR")][:3]
    check("dvdauthor accepts the app's XML — the jump form is legal",
          r.returncode == 0, errs)

    ts = os.path.join(work, "DVD/VIDEO_TS")
    made = sorted(os.listdir(ts)) if os.path.isdir(ts) else []
    # The IFO tables are the whole difference between a DVD-Video disc and a
    # folder of files a player will not touch.
    check("a real VIDEO_TS comes out, navigation tables and all",
          all(f in made for f in ("VIDEO_TS.IFO", "VIDEO_TS.BUP",
                                  "VTS_01_0.IFO", "VTS_01_1.VOB")), made)

    r = guest(burner.iso_cmd("DVD", "disc.iso", burner.disc_label("My Disc")))
    iso = os.path.join(work, "disc.iso")
    check("genisoimage masters the image from the app's own command",
          r.returncode == 0 and os.path.exists(iso), r.stdout[-300:])
    if os.path.exists(iso):
        with open(iso, "rb") as fh:
            image = fh.read()
        check("the image is ISO9660", image[32769:32774] == b"CD001",
              image[32769:32774])
        # Without UDF a set-top player is a coin toss, and this is the flag
        # xorriso cannot supply — the reason cdrkit is the media backend.
        check("the image carries a UDF filesystem",
              b"NSR02" in image[:400000] or b"NSR03" in image[:400000])
        check("VIDEO_TS is in the image", b"VIDEO_TS" in image)
        check("the disc carries the name the person typed",
              image[32808:32840].decode("ascii", "replace").strip()
              == "MY_DISC",
              image[32808:32840].decode("ascii", "replace").strip())
    _shutil.rmtree(work, ignore_errors=True)


# ============================================================== the verdict
# ---------------------------------------------------------------------------
#  A cancelled burn must actually stop — including what the tool started
# ---------------------------------------------------------------------------
# growisofs execs mkisofs, dvdauthor runs its own muxers, ffmpeg spawns for
# filters. terminate() reaches only the process we launched, and the survivor
# keeps the write end of the stdout pipe open — so the read loop never sees EOF
# and a cancelled burn HANGS with the drive still being written. Driven against
# a real process tree rather than a mock, because the whole defect lives in the
# relationship between a parent, a child, and a pipe.
import signal
import subprocess
import time


class _CancelAfter:
    """A job that reports cancelled once the burn has actually started.

    `cancelled` is a PROPERTY here because that is what it is on the real
    nbjobs.Job (which delegates to the cancel token's own property). It was a
    method, and this check therefore certified a contract that does not exist:
    _step's `job.cancelled()` passed here and raised TypeError on the worker
    thread of every real burn, so no disc was ever written. A fake has to be
    the shape of the thing it stands in for.
    """

    def __init__(self):
        self.cancels = 0

    def progress(self, *_a, **_k):
        pass

    def checkpoint(self):
        pass

    @property
    def cancelled(self):
        self.cancels += 1
        return self.cancels > 1


# A shell that spawns a child holding the same stdout, then sleeps. The child
# outlives a terminate() aimed at the parent, exactly like mkisofs under
# growisofs.
# The child carries a MARKER only this suite uses. It used to be a bare
# `sleep 60`, and the leftover check below both matched and SIGKILLed every
# `sleep 60` on the machine — including other people's background jobs, which
# is how this suite was killing unrelated work on the developer's box. A test
# may reap what IT started and nothing else.
MARK = "nb-burner-selftest-child"
SCRIPT = ("python3 -c \"import subprocess,sys,time;"
          "p=subprocess.Popen(['sleep','60','%s']);" % MARK +
          "print('burning', flush=True);"
          "sys.stdout.flush();"
          "time.sleep(60)\"")

# RUN IT ON A THREAD WITH A DEADLINE, so this check FAILS BY NAME when the
# defect is present instead of hanging until the batch runner kills the whole
# suite. A suite that hangs on a defect reads as an infrastructure flake and
# gets re-run; it has to say what it found. (Learned the hard way: the first
# version of this check called _step directly, and sabotaging the process group
# took the entire file past its timeout with no verdict at all.)
import threading

import nbjobs                                                    # noqa: E402

check("the fake job matches nbjobs.Job's own cancellation contract",
      isinstance(nbjobs.Job.__dict__.get("cancelled"), property)
      and isinstance(_CancelAfter.__dict__.get("cancelled"), property),
      (type(nbjobs.Job.__dict__.get("cancelled")).__name__,
       type(_CancelAfter.__dict__.get("cancelled")).__name__))

job = _CancelAfter()
done = threading.Event()


def _drive():
    try:
        burner._step(job, ["/bin/sh", "-c", SCRIPT], "burning")
    except Exception:                                            # noqa: BLE001
        pass                      # a cancelled step raising is fine
    finally:
        done.set()


started = time.time()
threading.Thread(target=_drive, daemon=True).start()
stopped = done.wait(20)
elapsed = time.time() - started
check("a cancelled burn stops instead of hanging on the pipe", stopped,
      "still running after %.1fs" % elapsed)

# And nothing it started is left running on the drive.
leftover = subprocess.run(["pgrep", "-f", "sleep 60 " + MARK],
                          capture_output=True,
                          text=True, timeout=10).stdout.split()
check("a cancelled burn leaves no tool still running", not leftover,
      "still alive: %r" % leftover)
for pid in leftover:                       # never leave the machine dirtier
    try:
        os.kill(int(pid), signal.SIGKILL)
    except (OSError, ValueError):
        pass


print("\n%d checks, %d passed, %d FAILED"
      % (CHECKS[0], CHECKS[0] - len(FAILS), len(FAILS)))
if FAILS:
    print("RESULT: FAILED")
    for f in FAILS:
        print("   - %s" % f)
else:
    print("RESULT: ALL PASS")
sys.exit(len(FAILS))
