#!/usr/bin/env python3
"""Media Viewer, driven the way a person drives it.

One real MediaViewer in an offscreen holder (tools/appdrive.py), real files on
disk under a private NB_HOME, real key presses through the app's own key ladder.
Every check below is named after the thing a person would notice, and each one
goes RED against the tree as it was before this round:

  M-01  Delete during an in-flight decode took the file that was still opening
  M-02  the filmstrip never scrolled, so the current thumbnail sat off-screen
  M-03  Info printed the decode budget's size as the file's dimensions
  M-04  three zoom-ins from fit landed on the top-left corner
  M-05  a zoom click at the cap re-scaled for nothing, on the GTK thread
  M-06  a move to the Trash said nothing and could not be undone
  M-07  a file moved away behind the viewer was reported as damaged
  M-08  F did nothing with a photograph, and the slideshow ran inside chrome
  M-09  a video's filmstrip claimed the folder held no images
  M-10  viewer shortcuts fired underneath the About card
  M-11  Modified printed an ISO stamp the rest of the OS does not use
  M-12  Rotate relabelled the file's dimensions as the on-screen size

Second pass, after the fix wave — each of these also goes RED on the tree as it
stood before it:

  M-13  leaving fullscreen handed back the Info panel the person had hidden
  M-14  a slideshow ended by Delete left the app stranded in its fullscreen
  M-15  trashing a film emptied a viewer whose folder was full of photographs
  M-16  a move to the Trash that FAILED took the picture off the stage
  M-17  a disabled tool was drawn pixel for pixel identical to a live one
  M-18  narrowing the window scrolled the current thumbnail off the strip
  M-19  closing mid-fullscreen left the desktop panel stood down

    tools/guestrun.sh python3 tools/media_realuse_selftest.py   -> exit 0 on PASS
"""
import os
import shutil
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))

ROOT = tempfile.mkdtemp(prefix="nb-media-realuse-")
os.environ["NB_DRIVE_HOME_ROOT"] = ROOT
HOME = os.path.join(ROOT, "media")          # appdrive's private NB_HOME
os.makedirs(HOME, exist_ok=True)

import appdrive                                                   # noqa: E402
import cairo                                                      # noqa: E402
from gi.repository import GdkPixbuf                               # noqa: E402

RESULTS = []


def check(name, fn):
    """Run one check. A check reports FAIL by name; it never falls over."""
    try:
        ok, detail = fn()
    except Exception as exc:                                      # noqa: BLE001
        ok, detail = False, "raised %s: %s" % (type(exc).__name__, exc)
    RESULTS.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name
          + ("" if ok else "  <- %s" % (detail,)))


# ---- fixtures ---------------------------------------------------------------
def picture(path, width, height, rgb, fmt="png"):
    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, width, height)
    pb.fill((rgb << 8) | 0xFF)
    opts = (["quality"], ["70"]) if fmt == "jpeg" else ([], [])
    pb.savev(path, fmt, opts[0], opts[1])
    return path


def folder(name):
    path = os.path.join(HOME, name)
    os.makedirs(path, exist_ok=True)
    return path


STEP = folder("Step")               # M-01: two pictures, one of them slow
A = picture(os.path.join(STEP, "a-first.png"), 400, 300, 0x2A6FCF)
B = picture(os.path.join(STEP, "b-second.png"), 3000, 2200, 0xCF7A2A)

WALK = folder("Walk")               # M-02/M-04/M-06/M-07/M-10/M-11/M-12
WALK_FILES = [picture(os.path.join(WALK, "img-%02d.png" % i), 400, 300,
                      0x203040 + i * 0x080402) for i in range(20)]
BIG = picture(os.path.join(WALK, "zz-wide.jpg"), 1600, 1000, 0x3C78C8, "jpeg")

LONE = folder("Lone")               # M-03/M-09
OVER = picture(os.path.join(LONE, "over-budget.jpg"), 6000, 4200, 0x808080,
               "jpeg")
CLIP = os.path.join(LONE, "clip.mp4")
with open(CLIP, "wb") as _fh:       # a video the strip must look past
    _fh.write(b"\x00" * 64)

SHOW = folder("Show")               # M-13/M-14/M-16/M-17/M-19
SHOW_FILES = [picture(os.path.join(SHOW, "show-%02d.png" % i), 500, 340,
                      0x506070 + i * 0x0A0604) for i in range(6)]

FILM = folder("Film")               # M-15: a clip in a folder of photographs
FILM_FILES = [picture(os.path.join(FILM, "reel-%02d.png" % i), 420, 300,
                      0x704020 + i * 0x060A04) for i in range(3)]
FILM_CLIP = os.path.join(FILM, "zz-holiday.mp4")
with open(FILM_CLIP, "wb") as _fh:
    _fh.write(b"\x00" * 64)

# M-18: a strip only a little wider than the window, so the LAST cell sits hard
# against the right edge — the one place where a narrower page pushes it out.
NARROW = folder("Narrow")
NARROW_FILES = [picture(os.path.join(NARROW, "edge-%02d.png" % i), 300, 220,
                        0x304050 + i * 0x0C0806) for i in range(9)]

d = appdrive.Drive("media")
app = d.app
media = d.mod
TRASH = os.path.join(media.HOME, ".Trash")


def trashed():
    if not os.path.isdir(TRASH):
        return []
    return sorted(x for x in os.listdir(TRASH) if not x.startswith("."))


def chrome():
    return (app._toolbar_w.get_visible(), app._info_w.get_visible(),
            app._film_w.get_visible())


# ---- M-01 -------------------------------------------------------------------
def m01():
    """Delete while the next picture is still decoding.

    No pump between the step and the key ON PURPOSE: a decode is delivered from
    a worker through the main loop, so with the loop untouched the stage is
    guaranteed to still be showing the first picture — exactly the window in
    which Delete used to take the second one."""
    app._display(A)
    d.pump(1.5)
    app._btn["next"].clicked()          # b-second starts decoding off-thread
    d.key("Delete")                     # ...pressed while a-first is on screen
    d.pump(2.0)
    return (trashed() == ["a-first.png"] and os.path.isfile(B)
            and not os.path.isfile(A),
            "trash %r a-first %r b-second %r"
            % (trashed(), os.path.isfile(A), os.path.isfile(B)))


check("M-01 Delete takes the picture on screen, not the one still decoding",
      m01)
shutil.rmtree(TRASH, ignore_errors=True)
picture(A, 400, 300, 0x2A6FCF)


# ---- M-02 -------------------------------------------------------------------
def m02():
    app._display(WALK_FILES[15])
    d.pump(2.5)
    adj = app._strip_scroll.get_hadjustment()
    btn = app._strip_btns.get(app._media_path)
    if btn is None:
        return False, "no strip cell for the current picture"
    got = btn.translate_coordinates(app._strip_row, 0, 0)
    x = got[-2] if got and len(got) >= 2 else btn.get_allocation().x
    middle = x + btn.get_allocation().width / 2.0
    seen = adj.get_value() <= middle <= adj.get_value() + adj.get_page_size()
    return (seen and adj.get_upper() > adj.get_page_size(),
            "cell middle %.0f, strip shows %.0f..%.0f of %.0f"
            % (middle, adj.get_value(),
               adj.get_value() + adj.get_page_size(), adj.get_upper()))


check("M-02 the filmstrip brings the current thumbnail into view", m02)


# ---- M-03 -------------------------------------------------------------------
def m03():
    app._display(OVER)
    d.pump(6.0)
    working = app._orig_pixbuf
    real = GdkPixbuf.Pixbuf.get_file_info(OVER)[1:]
    shown = app._info_vals["Dimensions"].get_text()
    budgeted = (working is not None
                and (working.get_width(), working.get_height()) != tuple(real))
    return (budgeted and shown == "%d × %d px" % (real[0], real[1]),
            "file %r working %r Info %r" % (
                tuple(real),
                working and (working.get_width(), working.get_height()), shown))


check("M-03 Info reports the file's own dimensions, not the decode budget's",
      m03)


# ---- M-04 -------------------------------------------------------------------
def m04():
    app._display(BIG)
    d.pump(2.0)
    for _ in range(3):
        app._btn["zoomin"].clicked()
        d.pump(0.4)
    h = app._scroll.get_hadjustment()
    v = app._scroll.get_vadjustment()
    hmid = (h.get_upper() - h.get_page_size()) / 2.0
    vmid = (v.get_upper() - v.get_page_size()) / 2.0
    zoomed = h.get_upper() > h.get_page_size()
    return (zoomed and abs(h.get_value() - hmid) <= 6
            and abs(v.get_value() - vmid) <= 6,
            "h %.0f (middle %.0f) v %.0f (middle %.0f)"
            % (h.get_value(), hmid, v.get_value(), vmid))


check("M-04 zoom keeps the point under the middle of the stage", m04)


# ---- M-05 -------------------------------------------------------------------
def m05_capped():
    """Clicks past the memory ceiling must not re-scale for nothing."""
    app._display(BIG)
    d.pump(2.0)
    for _ in range(20):                 # walk up to the cap
        app._btn["zoomin"].clicked()
        d.pump(0.15)
    renders = []
    real_render = app._render_image
    app._render_image = lambda *a, **k: (renders.append(1)
                                         or real_render(*a, **k))
    try:
        before = app._zoom
        app._btn["zoomin"].clicked()
        d.pump(0.3)
    finally:
        del app._render_image
    return (renders == [] and app._zoom == before,
            "%d re-render(s) at the cap, zoom %r -> %r"
            % (len(renders), before, app._zoom))


def m05_offthread():
    """A click at a deep zoom must hand the scale to a worker and come back."""
    app._display(BIG)
    d.pump(2.0)
    worst = 0.0
    for _ in range(18):
        start = time.monotonic()
        app._btn["zoomin"].clicked()
        worst = max(worst, time.monotonic() - start)
        d.pump(0.15)
    return worst < 0.15, "slowest zoom click held the main loop %.3fs" % worst


check("M-05 a zoom click at the cap does no scaling work", m05_capped)
check("M-05 a deep zoom click hands the scale off the main loop", m05_offthread)


# ---- M-06 -------------------------------------------------------------------
def m06_says():
    app._display(WALK_FILES[3])
    d.pump(2.0)
    d.key("Delete")
    d.pump(1.0)
    said = [t for t in d.texts() if "Trash" in t and "Ctrl+Z" in t]
    return (bool(said) and trashed() == ["img-03.png"],
            "on screen %r, trash %r" % (said, trashed()))


def m06_undo():
    d.key("z", ctrl=True)
    d.pump(2.0)
    return (os.path.isfile(WALK_FILES[3]) and trashed() == [],
            "back on disk %r, trash %r"
            % (os.path.isfile(WALK_FILES[3]), trashed()))


check("M-06 a move to the Trash says what it did and how to undo it", m06_says)
check("M-06 Ctrl+Z puts the trashed picture back", m06_undo)


# ---- M-07 -------------------------------------------------------------------
GONE = WALK_FILES[6]


def m07_says():
    app._display(WALK_FILES[5])
    d.pump(2.0)
    os.rename(GONE, GONE + ".moved")
    app._btn["next"].clicked()
    d.pump(2.5)
    return (app._surface_name == "notice"
            and app._notice_title.get_text() == "This file is no longer here",
            "surface %r title %r"
            % (app._surface_name, app._notice_title.get_text()))


def m07_drops():
    return (GONE not in app._siblings and GONE not in app._strip_btns
            and app._info_vals["Photo"].get_text() == "—",
            "in siblings %r in strip %r Photo %r"
            % (GONE in app._siblings, GONE in app._strip_btns,
               app._info_vals["Photo"].get_text()))


def m07_delete():
    d.key("Delete")
    d.pump(1.0)
    return (trashed() == []
            and app._notice_title.get_text() == "This file is no longer here",
            "trash %r title %r" % (trashed(), app._notice_title.get_text()))


check("M-07 stepping onto a file that has gone says so, not 'damaged'",
      m07_says)
check("M-07 ...and it leaves the folder count and the filmstrip", m07_drops)
check("M-07 Delete on a file that has gone says so instead of nothing",
      m07_delete)
os.rename(GONE + ".moved", GONE)


# ---- M-09 -------------------------------------------------------------------
def m09_strip():
    app._display(CLIP)
    d.pump(2.0)
    entries = app._strip_entries()
    return (entries == [OVER] and not app._strip_empty.get_visible(),
            "strip entries %r, empty-state showing %r (%r)"
            % (entries, app._strip_empty.get_visible(),
               app._strip_empty.get_text()))


def m09_tooltip():
    tip = app._btn["next"].get_tooltip_text()
    return (tip != "There are no other images in this folder.",
            "Next tooltip says %r over a folder holding %r"
            % (tip, os.path.basename(OVER)))


check("M-09 a video's filmstrip lists the folder's images", m09_strip)
check("M-09 ...and Previous/Next do not deny they are there", m09_tooltip)


# ---- M-10 -------------------------------------------------------------------
def m10():
    app._display(WALK_FILES[8])
    d.pump(2.0)
    d.menu_action("Media Viewer", "About")
    d.pump(0.6)
    was, zoom = app._media_path, app._zoom_lbl.get_text()
    d.key("Right")
    d.pump(1.5)
    d.key("plus")
    d.pump(0.4)
    moved, zoomed = app._media_path, app._zoom_lbl.get_text()
    d.key("Escape")
    d.pump(0.5)
    return (moved == was and zoomed == zoom,
            "Right moved %r -> %r, + zoomed %r -> %r"
            % (os.path.basename(was), os.path.basename(moved), zoom, zoomed))


check("M-10 viewer shortcuts do not fire underneath the About card", m10)


# ---- M-11 -------------------------------------------------------------------
def m11():
    app._display(WALK_FILES[9])
    d.pump(2.0)
    want = time.strftime("%d %b %Y, %H:%M",
                         time.localtime(os.stat(WALK_FILES[9]).st_mtime))
    got = app._info_vals["Modified"].get_text()
    return got == want, "Info says %r, the Finder's Get Info says %r" % (got,
                                                                         want)


check("M-11 Modified uses the same date phrase as the Finder's Get Info", m11)


# ---- M-12 -------------------------------------------------------------------
def m12():
    app._display(BIG)
    d.pump(2.0)
    before = app._info_vals["Dimensions"].get_text()
    app._btn["rotate"].clicked()
    d.pump(1.5)
    after = app._info_vals["Dimensions"].get_text()
    turned = app._orig_pixbuf.get_width() < app._orig_pixbuf.get_height()
    return (turned and before == after == "1600 × 1000 px",
            "picture turned %r, Dimensions %r -> %r" % (turned, before, after))


check("M-12 Rotate leaves the file's Dimensions alone", m12)


# ---- M-08 -------------------------------------------------------------------
def m08_key():
    app._display(BIG)
    d.pump(2.0)
    before = chrome()
    d.key("f")
    d.pump(0.4)
    filled = chrome()
    d.key("Escape")
    d.pump(0.4)
    return (before == (True, True, True) and filled == (False, False, False)
            and chrome() == (True, True, True),
            "before %r with F %r after Esc %r" % (before, filled, chrome()))


def m08_slideshow():
    app._display(WALK_FILES[1])
    d.pump(2.0)
    app._btn["play"].clicked()
    d.pump(0.5)
    running = bool(app._slideshow_id)
    filled = chrome()
    app._btn["play"].clicked()
    d.pump(0.5)
    return (running and filled == (False, False, False)
            and chrome() == (True, True, True),
            "running %r chrome while playing %r after stop %r"
            % (running, filled, chrome()))


check("M-08 F fills the screen with a photograph", m08_key)
check("M-08 a slideshow runs without the chrome around it", m08_slideshow)


# ---- M-13 -------------------------------------------------------------------
def m13():
    """Hide the Info panel from the View menu, fill the screen, come back.

    Fullscreen hides ALL the chrome, and on the way out it used to show all of
    it again — so a panel the person had deliberately turned off reappeared,
    with the View menu now offering to hide it a second time."""
    app._display(SHOW_FILES[2])
    d.pump(1.5)
    app._toggle_widget("infopanel")          # View ▸ Hide Info Panel
    hidden = not app._info_w.get_visible()
    d.key("f")
    d.pump(0.4)
    full = app._stage_full and not app._info_w.get_visible()
    d.key("Escape")
    d.pump(0.6)
    back = app._info_w.get_visible()
    if not back:                             # leave it up for the rest
        app._toggle_widget("infopanel")
        d.pump(0.2)
    return (hidden and full and not back and app._toolbar_w.get_visible(),
            "hidden %r fullscreen %r info after Esc %r toolbar %r"
            % (hidden, full, back, app._toolbar_w.get_visible()))


check("M-13 leaving fullscreen does not hand back a panel the person hid", m13)


# ---- M-14 -------------------------------------------------------------------
def m14():
    """A slideshow goes fullscreen by itself; the toolbar's Stop button goes
    with it. So every way a person can actually end one — Delete, an arrow key,
    a filmstrip pick — has to give that fullscreen back, or they are left with
    no menu bar, no toolbar and nothing to say the slideshow has ended."""
    app._display(SHOW_FILES[0])
    d.pump(1.5)
    app._btn["play"].clicked()
    d.pump(0.5)
    started = bool(app._slideshow_id) and app._stage_full and app._slide_full
    d.key("Delete")                          # ends the slideshow
    d.pump(2.0)
    ended = not app._slideshow_id and not app._stage_full
    d.key("z", ctrl=True)                    # put the picture back
    d.pump(1.5)
    return (started and ended and chrome() == (True, True, True),
            "started %r ended %r chrome %r" % (started, ended, chrome()))


check("M-14 a slideshow ended by Delete gives back the fullscreen it took", m14)


# ---- M-15 -------------------------------------------------------------------
def m15():
    """A video is its own standalone list of one, so "no siblings left" said
    nothing about the folder: trashing a clip out of a folder of photographs
    emptied the whole viewer and left a filmstrip reading "No images"."""
    app._display(FILM_CLIP)
    d.pump(1.5)
    strip_before = len(app._strip_btns)
    app._on_trash()
    d.pump(2.0)
    shown = app._shown_path
    return (strip_before == 3 and shown in FILM_FILES
            and len(app._strip_btns) == 3
            and app._surface_name == "image"
            and all(os.path.isfile(p) for p in FILM_FILES),
            "strip before %r after %r shown %r surface %r"
            % (strip_before, len(app._strip_btns), shown, app._surface_name))


check("M-15 trashing a film keeps the folder's photographs", m15)


# ---- M-16 -------------------------------------------------------------------
def m16():
    """A Move to Trash that fails must not take the picture with it. Nothing
    moved and nothing was lost, but the stage was replaced by a full error
    card — a move that failed read exactly like a file that had gone bad, over
    a toolbar still reporting the zoom of a picture no longer on screen."""
    app._display(SHOW_FILES[4])
    d.pump(1.5)
    held = app._orig_pixbuf
    real = media.nbapp.atomic_write_text
    media.nbapp.atomic_write_text = lambda *_a, **_k: (_ for _ in ()).throw(
        OSError("read-only trash"))
    try:
        d.key("Delete")
        d.pump(1.0)
    finally:
        media.nbapp.atomic_write_text = real
    said = app._flash_lbl.get_text()
    return (app._surface_name == "image" and app._orig_pixbuf is held
            and os.path.isfile(SHOW_FILES[4]) and bool(said.strip()),
            "surface %r pixbuf kept %r file %r said %r"
            % (app._surface_name, app._orig_pixbuf is held,
               os.path.isfile(SHOW_FILES[4]), said))


check("M-16 a Move to Trash that fails leaves the picture on the stage", m16)


# ---- M-17 -------------------------------------------------------------------
def glyph_ink(name):
    """The darkest pixel inside one toolbar button, rendered for real.

    Synchronous widget.draw() into a surface of the whole window, then the
    button's own box read back out of it — the same instrument the shots use,
    so what this measures is what is on the screen."""
    btn = app._btn[name]
    root = d.off.get_child()
    got = btn.translate_coordinates(root, 0, 0)
    if not got or len(got) < 2:
        return None
    x0, y0 = int(got[-2]), int(got[-1])
    alloc = btn.get_allocation()
    d.pump(0.15)
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, d.w, d.h)
    cr = cairo.Context(surf)
    cr.set_source_rgb(1, 1, 1)
    cr.paint()
    root.draw(cr)
    surf.flush()
    data = bytes(surf.get_data())
    stride = surf.get_stride()
    dark = 255.0
    for y in range(max(0, y0), min(d.h, y0 + alloc.height)):
        for x in range(max(0, x0), min(d.w, x0 + alloc.width)):
            off = y * stride + x * 4
            lum = (0.299 * data[off + 2] + 0.587 * data[off + 1]
                   + 0.114 * data[off])
            if lum < dark:
                dark = lum
    return dark


def m17():
    """These glyphs are drawn by the app itself, and GTK3 applies no
    insensitive effect to an image a widget was handed: every greyed control in
    this toolbar rendered identically to a live one, and the only thing telling
    the two apart was a tooltip you had to hover to find."""
    app._display(SHOW_FILES[1])
    d.pump(1.5)
    live = glyph_ink("zoomin")
    app._btn["zoomin"].set_sensitive(False)
    d.pump(0.2)
    off = glyph_ink("zoomin")
    app._update_controls()
    d.pump(0.2)
    if live is None or off is None:
        return False, "could not place the button in the render"
    return (off - live >= 25,
            "live glyph ink %.0f, disabled glyph ink %.0f" % (live, off))


check("M-17 a disabled tool is drawn lighter than a live one", m17)


# ---- M-18 -------------------------------------------------------------------
def m18():
    """A narrower window is a shorter strip. The cell that had been centred
    goes off the end of it, and the strip stayed exactly where it was — showing
    the start of a folder you were nowhere near."""
    app._display(NARROW_FILES[-1])
    d.pump(2.5)
    d.resize(720, 560)
    d.pump(0.8)
    adj = app._strip_scroll.get_hadjustment()
    btn = app._strip_btns.get(app._media_path)
    if btn is None:
        return False, "no strip cell for the current picture"
    got = btn.translate_coordinates(app._strip_row, 0, 0)
    x = got[-2] if got and len(got) >= 2 else btn.get_allocation().x
    width = btn.get_allocation().width
    seen = (adj.get_value() <= x
            and x + width <= adj.get_value() + adj.get_page_size())
    detail = ("cell %.0f..%.0f, strip shows %.0f..%.0f"
              % (x, x + width, adj.get_value(),
                 adj.get_value() + adj.get_page_size()))
    d.resize(*appdrive.PANEL)
    d.pump(0.6)
    return seen, detail


check("M-18 narrowing the window keeps the current thumbnail in the strip",
      m18)


# ---- M-19 -------------------------------------------------------------------
def m19():
    """Closing the window mid-fullscreen left the flag file behind holding this
    process's token, so the desktop's menu bar stayed stood down until the
    panel noticed the process had gone. It is one line to hand it back."""
    media.VIDEO_FULL_FLAG = os.path.join(ROOT, "video-fullscreen")
    app._display(SHOW_FILES[3])
    d.pump(1.5)
    d.key("f")
    d.pump(0.4)
    stood_down = app._stage_full and os.path.exists(media.VIDEO_FULL_FLAG)
    d.close()                                # the drive ends here
    return (stood_down and not os.path.exists(media.VIDEO_FULL_FLAG),
            "flag while fullscreen %r, flag after close %r"
            % (stood_down, os.path.exists(media.VIDEO_FULL_FLAG)))


check("M-19 closing mid-fullscreen stands the desktop panel back up", m19)


d.close()
shutil.rmtree(ROOT, ignore_errors=True)

failed = [name for name, ok in RESULTS if not ok]
print("\nMEDIA REAL-USE SELFTEST: %d checks, %d failed"
      % (len(RESULTS), len(failed)))
print("RESULT: " + ("PASS" if not failed else "FAILED"))
sys.exit(1 if failed else 0)
