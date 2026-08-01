"""
Notebook OS icon set — 24x24 line icons drawn directly with cairo, matching
the design language (1.6 px stroke, round caps, ink #1A1916). Rendering natively
keeps them crisp at any size and needs no icon-theme package.

Each icon is a list of drawing ops in a 24x24 coordinate space. `render(name,
size, color)` returns a GdkPixbuf ready for a Gtk.Image.
"""
import io
import math
import cairo
import gi
gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gtk  # noqa: E402


def _hex(c):
    c = c.lstrip("#")
    return (int(c[0:2], 16) / 255, int(c[2:4], 16) / 255, int(c[4:6], 16) / 255)


# op forms: ("M",x,y) move, ("L",x,y) line, ("C",cx,cy,r) circle,
#           ("R",x,y,w,h) rect, ("P", "svg-ish path") — we keep it to M/L/C/R/A
ICONS = {
    # A sheet of paper with a dog-eared corner and three text lines. The plain
    # framed page it used to be was a near-copy of "ebook" (same rectangle, same
    # three rules) — the two apps were indistinguishable in the Applications
    # folder. The folded corner is the universal "document you write" mark and
    # is what now separates the word processor from the reader.
    "writer":     [("M", 5.5, 3), ("L", 14, 3), ("L", 18.5, 7.5), ("L", 18.5, 21), ("L", 5.5, 21), ("L", 5.5, 3),
                   ("M", 14, 3), ("L", 14, 7.5), ("L", 18.5, 7.5),
                   ("M", 8.5, 11.5), ("L", 15.5, 11.5), ("M", 8.5, 15), ("L", 15.5, 15), ("M", 8.5, 18.5), ("L", 12.5, 18.5)],
    "novel":      [("M", 12, 6), ("L", 12, 19), ("R", 4, 5, 8, 14), ("R", 12, 5, 8, 14)],
    # A mortarboard. Authored on 2.5-21.5 it was the widest glyph in the set and
    # rendered ~20% larger and heavier than everything beside it in the
    # Applications grid; the same shape now sits in the 4-20 optical box the
    # other app icons share.
    "academic":   [("M", 4, 9.6), ("L", 12, 5.5), ("L", 20, 9.6), ("L", 12, 13.7), ("L", 4, 9.6), ("M", 7.4, 11.3), ("L", 7.4, 16.4), ("M", 16.6, 11.3), ("L", 16.6, 16.4)],
    "journal":    [("R", 5, 3.5, 14, 17), ("M", 9, 3.5), ("L", 9, 20.5)],
    "screenplay": [("R", 3.5, 8.5, 17, 11.5), ("M", 8, 8.5), ("L", 9.2, 5.3), ("M", 13, 8.5), ("L", 14.2, 5.8)],
    "tasks":      [("R", 4, 4, 16, 16), ("M", 8, 12.2), ("L", 10.8, 15), ("L", 16, 9)],
    "calendar":   [("R", 4, 5, 16, 15), ("M", 4, 9.2), ("L", 20, 9.2), ("M", 8, 3.5), ("L", 8, 6.5), ("M", 16, 3.5), ("L", 16, 6.5)],
    # A dumbbell: the bar runs the full width and the four plates sit across
    # it, so the shape still reads at 16px in the Finder list where the plates
    # are only a couple of pixels apart.
    "workout":    [("M", 5, 12), ("L", 19, 12),
                   ("M", 8, 8.4), ("L", 8, 15.6),
                   ("M", 16, 8.4), ("L", 16, 15.6),
                   ("M", 5, 10.2), ("L", 5, 13.8),
                   ("M", 19, 10.2), ("L", 19, 13.8)],
    # A lidded cooking pot: knob, lid band, and the POT BODY below it. The body
    # was missing entirely — the icon was a lid and a handle bar floating over
    # nothing, which read as a stray rectangle rather than a pot.
    "cookbook":   [("M", 10.6, 7.4), ("L", 13.4, 7.4),
                   ("M", 5, 10.5), ("L", 19, 10.5), ("L", 19, 13), ("L", 5, 13), ("L", 5, 10.5),
                   ("M", 6.9, 13), ("L", 7.9, 19.4), ("L", 16.1, 19.4), ("L", 17.1, 13)],
    # A reading DEVICE: page of text above a bezel rule with a round home
    # button. An e-reader is a thing you hold, and drawing it as one is what
    # keeps it apart from "writer" (a sheet of paper) and "novel" (a book).
    # A place setting — plate between a fork and a knife — laid on a ruled card.
    # The Meal Planner sits beside the Cookbook in the Applications folder, so
    # it deliberately borrows none of the pot: this is the week's MENU BOARD,
    # planning (the card and its header rule) plus eating (the setting). The
    # card is left unhangered so it does not become a second Calendar either.
    "mealplanner":[("R", 3.5, 5, 17, 15), ("M", 3.5, 9.5), ("L", 20.5, 9.5),
                   ("C", 12, 15.2, 3.0),
                   ("M", 7, 12.2), ("L", 7, 18.2), ("M", 17, 12.2), ("L", 17, 18.2)],
    # (A bezel rule under the text was tried and dropped: at the 22px list size
    # it collided with the home button and the bottom of the icon went muddy.)
    "ebook":      [("R", 4.5, 3, 15, 18),
                   ("M", 8, 7), ("L", 16, 7), ("M", 8, 10.5), ("L", 16, 10.5), ("M", 8, 14), ("L", 12.5, 14),
                   ("C", 12, 18.2, 1.0)],
    "calculator": [("R", 5, 3, 14, 18), ("R", 8, 6, 8, 3), ("C", 9, 13, 0.6), ("C", 12, 13, 0.6), ("C", 15, 13, 0.6), ("C", 9, 16, 0.6), ("C", 12, 16, 0.6), ("C", 15, 16, 0.6)],
    "accounting": [("R", 3, 7, 18, 10), ("C", 12, 12, 2.4), ("C", 6, 12, 0.6), ("C", 18, 12, 0.6)],
    "contacts":   [("C", 12, 8.5, 3.2), ("M", 5.5, 19), ("L", 5.5, 17), ("L", 18.5, 17), ("L", 18.5, 19)],
    "messages":   [("R", 4, 5, 16, 10), ("M", 9, 15), ("L", 9, 19), ("L", 13, 15), ("C", 9, 10, 0.6), ("C", 12, 10, 0.6), ("C", 15, 10, 0.6)],
    "g2048":      [("R", 4, 4, 7, 7), ("R", 13, 4, 7, 7), ("R", 4, 13, 7, 7), ("R", 13, 13, 7, 7)],
    "tetris":     [("R", 4, 4, 5.3, 5.3), ("R", 9.3, 4, 5.3, 5.3), ("R", 14.6, 4, 5.3, 5.3), ("R", 9.3, 9.3, 5.3, 5.3)],
    # A game controller: rounded body, a d-pad cross on the left, two action
    # buttons on the right. Used by the GBA Emulator.
    "gamepad":    [("R", 3.5, 8, 17, 8.5), ("M", 8, 10.2), ("L", 8, 14.2), ("M", 6, 12.2), ("L", 10, 12.2), ("C", 15.7, 11, 1), ("C", 18, 13.2, 1)],
    # A map location pin: round head, tapering point, centre dot. Used by Maps.
    "mappin":     [("C", 12, 9, 5), ("M", 8.2, 12.6), ("L", 12, 20.5), ("L", 15.8, 12.6), ("C", 12, 9, 1.6)],
    # A globe: sphere with an equator and two meridian arcs. Used by Language.
    "globe":      [("C", 12, 12, 9), ("M", 3, 12), ("L", 21, 12), ("M", 12, 3), ("L", 12, 21), ("M", 12, 3), ("Q", 4.5, 12, 12, 21), ("M", 12, 3), ("Q", 19.5, 12, 12, 21)],
    # A game cartridge: body, top label band, and the two notch cuts at the
    # bottom edge. Used by the GBA SDK.
    "cartridge":  [("M", 6, 3.5), ("L", 18, 3.5), ("L", 18, 20.5), ("L", 6, 20.5), ("L", 6, 3.5), ("R", 8.5, 6, 7, 5), ("M", 9, 20.5), ("L", 9, 17.5), ("M", 15, 20.5), ("L", 15, 17.5)],
    "illustrator":[("M", 5, 19), ("L", 12, 4), ("L", 19, 19), ("M", 8.2, 13), ("L", 15.8, 13)],
    "sequencer":  [("M", 4, 12), ("L", 4, 16), ("M", 8, 8), ("L", 8, 18), ("M", 12, 5), ("L", 12, 19), ("M", 16, 9), ("L", 16, 17), ("M", 20, 11), ("L", 20, 15)],
    "video":      [("R", 3, 6, 12, 12), ("M", 15, 10), ("L", 21, 7), ("L", 21, 17), ("L", 15, 14)],
    "media":      [("R", 3, 5, 18, 14), ("C", 9, 10, 1.6), ("M", 4, 17), ("L", 9, 13), ("L", 13, 16), ("L", 16, 13.5), ("L", 20, 17)],
    "music":      [("M", 9, 17), ("L", 9, 6), ("L", 19, 4), ("L", 19, 15), ("C", 6.5, 17.5, 2.5), ("C", 16.5, 15.5, 2.5)],
    "packages":   [("M", 12, 3), ("L", 20, 7), ("L", 20, 17), ("L", 12, 21), ("L", 4, 17), ("L", 4, 7), ("L", 12, 3), ("M", 4, 7), ("L", 12, 11), ("L", 20, 7), ("M", 12, 11), ("L", 12, 21)],
    "signal":     [("A", 12, 18.6, 0.7), ("M", 9, 16.4), ("Q", 12, 14.4, 15, 16.4), ("M", 5.5, 14), ("Q", 12, 9, 18.5, 14)],
    "play":       [("M", 8, 5), ("L", 19, 12), ("L", 8, 19), ("L", 8, 5)],
    "stopsq":     [("R", 6, 6, 12, 12)],
    # two upright bars: the PAUSE action. A playing clip must offer pause, not
    # stop (stop would imply discarding the position), so the transport button
    # shows this while playing.
    "pause":      [("R", 7.5, 5, 3.5, 14), ("R", 13, 5, 3.5, 14)],
    # Window-control marks for the Mac-OS-7 title bar. Drawn (not typed) so they
    # never depend on a font carrying the glyph — the shipped Nimbus Sans does not,
    # and a missing glyph shows as a tofu box on real hardware.
    "wclose":     [("M", 8, 8), ("L", 16, 16), ("M", 16, 8), ("L", 8, 16)],
    "wzoom":      [("R", 7, 7, 10, 10), ("M", 7, 10.5), ("L", 17, 10.5)],
    "wshade":     [("M", 7.5, 12), ("L", 16.5, 12)],
    "rew":        [("M", 11, 5), ("L", 3, 12), ("L", 11, 19), ("L", 11, 5), ("M", 20, 5), ("L", 12, 12), ("L", 20, 19), ("L", 20, 5)],
    "ff":         [("M", 4, 5), ("L", 12, 12), ("L", 4, 19), ("L", 4, 5), ("M", 13, 5), ("L", 21, 12), ("L", 13, 19), ("L", 13, 5)],
    "folder":     [("M", 3, 8), ("L", 3, 18), ("L", 21, 18), ("L", 21, 9), ("L", 11, 9), ("L", 9, 6.8), ("L", 3, 6.8), ("L", 3, 8)],
    "home":       [("M", 4, 11), ("L", 12, 4), ("L", 20, 11), ("M", 6, 11), ("L", 6, 20), ("L", 18, 20), ("L", 18, 11), ("R", 10, 14, 4, 6)],
    "desktop":    [("R", 3, 5, 18, 12), ("M", 8.5, 20), ("L", 15.5, 20), ("M", 12, 17), ("L", 12, 20)],
    "disk":       [("R", 3, 6.5, 18, 11), ("C", 17, 12, 0.9), ("M", 6, 10), ("L", 12, 10)],
    "trash":      [("M", 5, 7), ("L", 19, 7), ("M", 7, 7), ("L", 8, 20), ("L", 16, 20), ("L", 17, 7), ("M", 9.5, 7), ("L", 9.5, 4.2), ("L", 14.5, 4.2), ("L", 14.5, 7)],
    "search":     [("C", 11, 11, 6), ("M", 15.4, 15.4), ("L", 20, 20)],
    "back":       [("M", 15, 5), ("L", 8, 12), ("L", 15, 19)],
    "backspace":  [("M", 3, 12), ("L", 8.5, 6.5), ("L", 20, 6.5), ("L", 20, 17.5), ("L", 8.5, 17.5), ("L", 3, 12), ("M", 12.5, 9.5), ("L", 17, 14.5), ("M", 17, 9.5), ("L", 12.5, 14.5)],
    "fwd":        [("M", 9, 5), ("L", 16, 12), ("L", 9, 19)],
    "up":         [("M", 5, 13), ("L", 12, 6), ("L", 19, 13), ("M", 12, 6), ("L", 12, 20)],
    # the mirror of "up" — for reordering (Send Back / move down), where
    # rotating the up glyph 180 degrees was the only option before
    "down":       [("M", 5, 11), ("L", 12, 18), ("L", 19, 11), ("M", 12, 18), ("L", 12, 4)],
    "viewlist":   [("C", 5, 6.5, 0.9), ("M", 9, 6.5), ("L", 20, 6.5), ("C", 5, 12, 0.9), ("M", 9, 12), ("L", 20, 12), ("C", 5, 17.5, 0.9), ("M", 9, 17.5), ("L", 20, 17.5)],
    "viewgrid":   [("R", 4, 4, 7, 7), ("R", 13, 4, 7, 7), ("R", 4, 13, 7, 7), ("R", 13, 13, 7, 7)],
    "check":      [("M", 5, 12.5), ("L", 10, 17.5), ("L", 19, 7)],
    "link":       [("C", 8.5, 15.5, 3.2), ("C", 15.5, 8.5, 3.2), ("M", 10.5, 13.5), ("L", 13.5, 10.5)],
    "quote":      [("M", 7, 8), ("L", 7, 12), ("L", 10, 12), ("L", 10, 8), ("L", 6, 8), ("M", 8.5, 12), ("L", 7, 15), ("M", 14, 8), ("L", 14, 12), ("L", 17, 12), ("L", 17, 8), ("L", 13, 8), ("M", 15.5, 12), ("L", 14, 15)],
    "plus":       [("M", 12, 5), ("L", 12, 19), ("M", 5, 12), ("L", 19, 12)],
    "star":       [("M", 12, 3), ("L", 14.23, 8.93), ("L", 20.56, 9.22), ("L", 15.61, 13.17), ("L", 17.29, 19.28), ("L", 12, 15.8), ("L", 6.71, 19.28), ("L", 8.39, 13.17), ("L", 3.44, 9.22), ("L", 9.77, 8.93), ("L", 12, 3)],
    "inbox":      [("M", 4, 13), ("L", 8, 13), ("L", 9.5, 15.5), ("L", 14.5, 15.5), ("L", 16, 13), ("L", 20, 13), ("M", 4, 13), ("L", 6.5, 6), ("L", 17.5, 6), ("L", 20, 13), ("L", 20, 18), ("L", 4, 18), ("L", 4, 13)],
    "bullet":     [("C", 5, 7, 1), ("M", 9, 7), ("L", 20, 7), ("C", 5, 12, 1), ("M", 9, 12), ("L", 20, 12), ("C", 5, 17, 1), ("M", 9, 17), ("L", 20, 17)],
    "number":     [("M", 10, 7), ("L", 20, 7), ("M", 10, 12), ("L", 20, 12), ("M", 10, 17), ("L", 20, 17), ("M", 4.3, 6.5), ("L", 5.7, 5.3), ("L", 5.7, 9), ("M", 4, 13.5), ("L", 6, 13.5), ("L", 4, 16.5), ("L", 6, 16.5)],
    "highlight":  [("M", 5, 19), ("L", 7.5, 18.4), ("L", 16.5, 9.4), ("L", 14.5, 7.4), ("L", 5.5, 16.4), ("L", 5, 19), ("M", 14.5, 6.5), ("L", 17.5, 9.5), ("M", 4, 21), ("L", 9, 21)],
    "toc":        [("M", 4, 6), ("L", 20, 6), ("M", 4, 12), ("L", 20, 12), ("M", 4, 18), ("L", 14, 18)],
    "alignleft":  [("M", 4, 6), ("L", 20, 6), ("M", 4, 10), ("L", 15, 10), ("M", 4, 14), ("L", 20, 14), ("M", 4, 18), ("L", 15, 18)],
    "aligncenter":[("M", 4, 6), ("L", 20, 6), ("M", 6.5, 10), ("L", 17.5, 10), ("M", 4, 14), ("L", 20, 14), ("M", 6.5, 18), ("L", 17.5, 18)],
    "alignright": [("M", 4, 6), ("L", 20, 6), ("M", 9, 10), ("L", 20, 10), ("M", 4, 14), ("L", 20, 14), ("M", 9, 18), ("L", 20, 18)],
    "alignjustify":[("M", 4, 6), ("L", 20, 6), ("M", 4, 10), ("L", 20, 10), ("M", 4, 14), ("L", 20, 14), ("M", 4, 18), ("L", 20, 18)],
    "indent":     [("M", 10, 6), ("L", 20, 6), ("M", 10, 10), ("L", 20, 10), ("M", 10, 14), ("L", 20, 14), ("M", 10, 18), ("L", 20, 18), ("M", 4, 8), ("L", 7, 12), ("L", 4, 16)],
    "outdent":    [("M", 10, 6), ("L", 20, 6), ("M", 10, 10), ("L", 20, 10), ("M", 10, 14), ("L", 20, 14), ("M", 10, 18), ("L", 20, 18), ("M", 7, 8), ("L", 4, 12), ("L", 7, 16)],
    "table":      [("R", 4, 5, 16, 14), ("M", 4, 10), ("L", 20, 10), ("M", 4, 15), ("L", 20, 15), ("M", 10, 5), ("L", 10, 19), ("M", 15, 5), ("L", 15, 19)],
    "eject":      [("M", 7, 13), ("L", 12, 6), ("L", 17, 13), ("L", 7, 13), ("M", 7, 16), ("L", 17, 16)],
    "library":    [("R", 4, 4, 5, 16), ("R", 10.5, 4, 5, 16), ("M", 17.5, 5.5), ("L", 20.5, 6.3), ("L", 17.5, 20.3), ("L", 14.5, 19.5), ("L", 17.5, 5.5)],
    "bookmark":   [("M", 6, 4), ("L", 18, 4), ("L", 18, 20), ("L", 12, 16.6), ("L", 6, 20), ("L", 6, 4)],
    "pencil":     [("M", 4, 20), ("L", 5, 16), ("L", 16.5, 4.5), ("L", 19.5, 7.5), ("L", 8, 19), ("L", 4, 20), ("M", 14, 7), ("L", 17, 10)],
    "brush":      [("M", 14, 4), ("L", 20, 10), ("L", 11.5, 17), ("L", 7, 12.5), ("L", 14, 4), ("M", 7, 12.5), ("L", 4.5, 16), ("L", 4, 20), ("L", 8, 19.5), ("L", 11.5, 17)],
    "eraser":     [("M", 4, 15), ("L", 12, 7), ("L", 18, 13), ("L", 12, 19), ("L", 8, 19), ("L", 4, 15), ("M", 4.5, 19), ("L", 20, 19)],
    "fill":       [("M", 8, 3), ("L", 17, 12), ("L", 10, 19), ("L", 3, 12), ("L", 8, 3), ("M", 8, 3), ("L", 8, 7), ("A", 19.5, 15.5, 1)],
    "picker":     [("M", 5.5, 20), ("L", 5.5, 17.5), ("L", 14, 9), ("L", 16.5, 11.5), ("L", 8, 20), ("L", 5.5, 20), ("M", 15, 10.5), ("L", 18, 7.5), ("L", 16.5, 6), ("L", 13.5, 9)],
    "line":       [("M", 5, 19), ("L", 19, 5)],
    "rect":       [("R", 4, 6, 16, 12)],
    # two offset sheets — the universal "make a copy of this" mark
    "duplicate":  [("M", 4, 15), ("L", 4, 4), ("L", 15, 4), ("R", 8, 8, 12, 12)],
    "ellipse":    [("C", 12, 12, 7)],
    "eye":        [("M", 3, 12), ("Q", 12, 5, 21, 12), ("M", 21, 12), ("Q", 12, 19, 3, 12), ("C", 12, 12, 2.4)],
    "eyeoff":     [("M", 3, 12), ("Q", 12, 5.5, 21, 12), ("M", 21, 12), ("Q", 12, 18.5, 3, 12), ("M", 4, 20), ("L", 20, 4)],
    "prev":       [("M", 18, 5), ("L", 9, 12), ("L", 18, 19), ("L", 18, 5), ("M", 6, 5), ("L", 6, 19)],
    "next":       [("M", 6, 5), ("L", 15, 12), ("L", 6, 19), ("L", 6, 5), ("M", 18, 5), ("L", 18, 19)],
    "zoomin":     [("C", 11, 11, 6), ("M", 15.4, 15.4), ("L", 20, 20), ("M", 11, 8.5), ("L", 11, 13.5), ("M", 8.5, 11), ("L", 13.5, 11)],
    "zoomout":    [("C", 11, 11, 6), ("M", 15.4, 15.4), ("L", 20, 20), ("M", 8.5, 11), ("L", 13.5, 11)],
    "rotate":     [("M", 19.5, 12), ("L", 17.6, 16.6), ("L", 12.7, 17.6), ("L", 8.6, 14.7), ("L", 8.1, 9.7), ("L", 11.6, 6.2), ("L", 16.5, 6.5), ("M", 16.5, 6.5), ("L", 15, 4.8), ("M", 16.5, 6.5), ("L", 14.4, 8)],
    "trfade":     [("R", 4, 6, 7, 12), ("R", 13, 6, 7, 12), ("M", 11, 12), ("L", 13, 12)],
    "trdissolve": [("R", 4, 6, 16, 12), ("A", 9, 10, 0.7), ("A", 13, 13, 0.7), ("A", 11, 15, 0.7), ("A", 15, 9, 0.7)],
    "trwipe":     [("R", 4, 6, 16, 12), ("M", 12, 6), ("L", 12, 18)],
    "trslide":    [("R", 4, 6, 16, 12), ("M", 9, 12), ("L", 16, 12), ("M", 13, 9), ("L", 16, 12), ("L", 13, 15)],
    "triris":     [("R", 4, 6, 16, 12), ("C", 12, 12, 3.4)],
    "trblack":    [("R", 4, 6, 16, 12), ("M", 4, 6), ("L", 20, 18)],
    "album":      [("C", 12, 12, 8.5), ("C", 12, 12, 2.2)],
    "artist":     [("C", 12, 8.5, 3.2), ("M", 5.5, 19), ("Q", 12, 15, 18.5, 19)],
    "vol":        [("M", 4, 9), ("L", 4, 15), ("L", 8, 15), ("L", 13, 19), ("L", 13, 5), ("L", 8, 9), ("L", 4, 9), ("M", 16.5, 8.5), ("Q", 19.5, 12, 16.5, 15.5)],
    "shuffle":    [("M", 3, 7), ("L", 7, 7), ("L", 17, 17), ("L", 21, 17), ("M", 3, 17), ("L", 7, 17), ("L", 10, 14), ("M", 14, 7), ("L", 21, 7), ("M", 18, 4), ("L", 21, 7), ("L", 18, 10), ("M", 18, 14), ("L", 21, 17), ("L", 18, 20)],
    "repeat":     [("M", 4, 10), ("L", 4, 8), ("Q", 4, 5, 7, 5), ("L", 19, 5), ("M", 16, 2), ("L", 19, 5), ("L", 16, 8), ("M", 20, 14), ("L", 20, 16), ("Q", 20, 19, 17, 19), ("L", 5, 19), ("M", 8, 22), ("L", 5, 19), ("L", 8, 16)],
    "box":        [("M", 12, 3), ("L", 20, 7), ("L", 20, 17), ("L", 12, 21), ("L", 4, 17), ("L", 4, 7), ("L", 12, 3), ("M", 4, 7), ("L", 12, 11), ("L", 20, 7), ("M", 12, 11), ("L", 12, 21)],
    "update":     [("M", 19.5, 12), ("L", 17.6, 16.6), ("L", 12.7, 17.6), ("L", 8.6, 14.7), ("L", 8.1, 9.7), ("L", 11.6, 6.2), ("L", 16.5, 6.5), ("M", 16.5, 6.5), ("L", 15, 4.8), ("M", 16.5, 6.5), ("L", 14.4, 8), ("M", 12, 9.5), ("L", 12, 12.5), ("L", 14.2, 13.6)],
    "sources":    [("R", 3, 4.5, 18, 6), ("R", 3, 13.5, 18, 6), ("A", 6.3, 7.5, 0.75), ("A", 6.3, 16.5, 0.75)],
    "sys":        [("C", 12, 12, 3), ("M", 12, 4.4), ("L", 12, 6.7), ("M", 12, 17.3), ("L", 12, 19.6), ("M", 4.4, 12), ("L", 6.7, 12), ("M", 17.3, 12), ("L", 19.6, 12), ("M", 6.6, 6.6), ("L", 8.3, 8.3), ("M", 15.7, 15.7), ("L", 17.4, 17.4), ("M", 17.4, 6.6), ("L", 15.7, 8.3), ("M", 8.3, 15.7), ("L", 6.6, 17.4)],
    # ---- one glyph per app -------------------------------------------------
    # Four apps used to borrow somebody else's mark: Settings and System
    # Monitor were the SAME gear, Terminal wore "toc" (which is also the glyph
    # for a file the OS cannot open, so Terminal.app looked like junk on the
    # disk), the installer wore the Devices disk and the GBA SDK wore the .gba
    # ROM cartridge. Each now has its own.
    #
    # Terminal: a screen with a prompt caret and the cursor rule beneath it —
    # the shape every command line has had since VT100s. No title band, so it
    # cannot be mistaken for the IDE's window at 16px.
    "terminal":   [("R", 3.5, 5, 17, 14),
                   ("M", 7, 9.7), ("L", 10, 12.2), ("L", 7, 14.7),
                   ("M", 12, 14.7), ("L", 17, 14.7)],
    # System Monitor: an activity graph — axes and a trace. A gauge dial turned
    # to mush at 16px and the bar-chart reading was already taken by
    # "sequencer", so the line trace is what stays legible and stays distinct.
    "sysmon":     [("M", 4, 4.5), ("L", 4, 19.5), ("L", 20, 19.5),
                   ("M", 6.5, 15.5), ("L", 10, 10.5), ("L", 13.5, 13.5), ("L", 19, 6.5)],
    # Installer: an arrow coming DOWN into a drive. The drive deliberately
    # echoes the "disk" glyph (same 18-wide body, same activity dot) because
    # that is exactly what the app does — puts the system onto that disk — but
    # the arrow means it can never be read as the Devices rail.
    "installer":  [("R", 3, 13.5, 18, 7), ("C", 17.5, 17, 0.9),
                   ("M", 12, 2.5), ("L", 12, 10.5),
                   ("M", 8, 6.8), ("L", 12, 10.8), ("L", 16, 6.8)],
    # GBA SDK: angle brackets around a slash, the universal source-code mark.
    # Drawn frameless (like illustrator/music/packages) rather than as a window
    # with brackets inside, because a second bordered rectangle would have read
    # as the Terminal's twin in the 16px list.
    "gbasdk":     [("M", 8.5, 7.5), ("L", 4, 12), ("L", 8.5, 16.5),
                   ("M", 15.5, 7.5), ("L", 20, 12), ("L", 15.5, 16.5),
                   ("M", 13.2, 6), ("L", 10.8, 18)],
}

# Five apps' glyphs are not named after their module, because the glyph names a
# THING ("gamepad") and the module names the app ("gbaemu"), or because the app
# was renamed after its glyph was drawn ("academics" -> "academic"). Anything
# that turns a module name into a glyph has to go through here, or those five
# silently fall back to a generic mark: Packages did exactly that, and listed
# Academics, GBA Emulator, Language and Maps with the same "sys" starburst that
# Settings wears, four apparent duplicates in a list where Finder showed the
# right icons. finder.ICON_ALIAS is this same dict.
ALIAS = {"settings": "sys", "language": "globe", "maps": "mappin",
         "gbaemu": "gamepad", "academics": "academic"}


def glyph_for(module, fallback="sys"):
    """The glyph a DE module wears: its own name, its alias, else `fallback`."""
    if module in ICONS:
        return module
    name = ALIAS.get(module)
    return name if name in ICONS else fallback


# Glyphs that MEAN a direction rather than just happening to point somewhere.
# Under a right-to-left language "previous" is on the right, so a left-pointing
# chevron for Previous Month is simply wrong — GTK mirrors the buttons but a
# cairo path knows nothing about direction, so these have to be flipped by hand.
# Deliberately NOT here: play/pause/rew/ff (transport controls follow the tape,
# not the text), and anything whose shape is not an arrow.
_DIRECTIONAL = {"back", "fwd", "prev", "next", "indent", "outdent"}


def _is_rtl():
    try:
        return Gtk.Widget.get_default_direction() == Gtk.TextDirection.RTL
    except Exception:
        return False


def draw(ctx, name, size, color="#1A1916", width=1.6, mirror=None):
    ops = ICONS.get(name)
    if not ops:
        ops = [("R", 5, 5, 14, 14)]
    s = size / 24.0
    # Start from a clean path. A caller that has drawn anything of its own into
    # this context leaves a current point behind, and the first circle/arc op
    # here would then be joined to it by a stray line straight across the icon
    # (cairo's arc() lines to the arc's start when a current point exists).
    ctx.new_path()
    ctx.save()
    ctx.scale(s, s)
    if mirror is None:
        mirror = name in _DIRECTIONAL and _is_rtl()
    if mirror:
        ctx.translate(24, 0)          # icons are authored on a 24x24 grid
        ctx.scale(-1, 1)
    r, g, b = _hex(color)
    ctx.set_source_rgb(r, g, b)
    ctx.set_line_width(width)
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    ctx.set_line_join(cairo.LINE_JOIN_ROUND)
    started = False
    for op in ops:
        k = op[0]
        if k == "M":
            ctx.move_to(op[1], op[2]); started = True
        elif k == "L":
            ctx.line_to(op[1], op[2])
        elif k == "R":
            if started:
                ctx.stroke()
            ctx.rectangle(op[1], op[2], op[3], op[4]); ctx.stroke(); started = False
        elif k == "C":
            if started:
                ctx.stroke(); started = False
            ctx.arc(op[1], op[2], op[3], 0, 2 * math.pi); ctx.stroke()
        elif k == "A":
            if started:
                ctx.stroke(); started = False
            ctx.arc(op[1], op[2], op[3], 0, 2 * math.pi); ctx.fill()
        elif k == "Q":
            # quadratic-ish via arc approximation: treat as an arc through 3 pts
            ctx.curve_to(op[1], op[2], op[1], op[2], op[3], op[4])
    if started:
        ctx.stroke()
    ctx.restore()


# A rendered icon is a pure, deterministic function of (name, size, color,
# width), and a GdkPixbuf is immutable as far as consumers go (Gtk.Image only
# refs it, never mutates), so it is safe to build once and share across every
# widget. Memoizing here collapses all repeated same-icon renders — which
# dominate list rebuilds / folder opens — to a single PNG round-trip.
_PIXBUF_CACHE = {}


def pixbuf(name, size, color="#1A1916", width=1.6):
    """cairo-drawn icon -> GdkPixbuf. We route through PNG bytes (pure pycairo
    write_to_png + a PixbufLoader) rather than Gdk.pixbuf_get_from_surface,
    which would need PyGObject's cairo foreign-type bridge (not built here).

    The result is memoized on (name, size, color, width); the returned pixbuf is
    shared and must be treated as read-only by callers (which Gtk.Image is)."""
    # Direction is part of the key: the same name draws a different glyph in a
    # right-to-left language, and a shared cache would hand back the wrong one.
    key = (name, size, color, width, _is_rtl() and name in _DIRECTIONAL)
    cached = _PIXBUF_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        ctx = cairo.Context(surf)
        draw(ctx, name, size, color, width)
        surf.flush()
        buf = io.BytesIO()
        surf.write_to_png(buf)
        loader = GdkPixbuf.PixbufLoader.new_with_type("png")
        loader.write(buf.getvalue())
        loader.close()
        pb = loader.get_pixbuf()
        if pb is not None:
            _PIXBUF_CACHE[key] = pb
            return pb
    except Exception:
        # A missing gdk-pixbuf PNG loader (or any cairo/loader failure) must not
        # crash the app at construction time — icons degrade to blank instead.
        pass
    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, size, size)
    pb.fill(0x00000000)
    _PIXBUF_CACHE[key] = pb
    return pb


def style_search_entry(entry, size=15, color="#9A9484"):
    """Give a Gtk.SearchEntry OUR magnifier instead of the icon theme's.

    Gtk.SearchEntry is the one widget in this OS that reaches outside itself for
    an image: it asks the icon theme for "edit-find-symbolic". This OS ships no
    icon theme that has it — /usr/share/icons holds hicolor (four unrelated app
    icons: cups, htop, compton) and the notebook CURSOR theme, nothing else — so
    that lookup can only ever land on GTK's internal fallback. On a developer
    machine with a full theme installed it comes back as a blue, shaded 3-D
    pixmap: the only blue and the only non-flat icon anywhere in a flat warm-paper
    OS, sitting in the Finder's toolbar. With no theme at all it resolves to
    image-missing, and a bare SearchEntry then fails to paint.

    Music, Packages and Contacts never had the problem because they build their
    own search fields out of an nbicons glyph. The seven that use SearchEntry
    (finder, journal, academics, novel, screenplay, writer, nbpicker) now draw
    the same glyph through here, so every search field in the OS matches and none
    of them depends on anything outside the image.

    Overriding the PRIMARY icon is sufficient and verified: the entry keeps
    working, including once text is typed into it. Cosmetic, so never fatal.
    """
    try:
        entry.set_icon_from_pixbuf(Gtk.EntryIconPosition.PRIMARY,
                                   pixbuf("search", size, color))
        entry.set_icon_activatable(Gtk.EntryIconPosition.PRIMARY, False)
    except Exception:                                            # noqa: BLE001
        pass
