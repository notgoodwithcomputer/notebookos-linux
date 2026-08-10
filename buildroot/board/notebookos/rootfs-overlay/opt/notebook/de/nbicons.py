"""
Notebook OS icon set — bold 24x24 mid-century pictograms drawn with cairo.
Filled ink silhouettes and paper-coloured cutouts replace the former line set.
Rendering natively
keeps them crisp at any size and needs no icon-theme package.

Each icon is a list of drawing ops in a 24x24 coordinate space. `render(name,
size, color)` returns a GdkPixbuf ready for a Gtk.Image.
"""
import io
import math
import os
import cairo
import gi
gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GObject, Gtk  # noqa: E402


def _hex(c):
    c = c.lstrip("#")
    return (int(c[0:2], 16) / 255, int(c[2:4], 16) / 255, int(c[4:6], 16) / 255)


# Op table (all coordinates live on the 24x24 authoring grid):
# M move, L line, R rectangle, RR rounded rectangle, C stroked circle,
# A filled circle, Q quadratic-like curve, B cubic Bezier, AR arc segment,
# F fill current path. PF fills a polygon; RF/RRF fill a rectangle/rounded
# rectangle; E fills an ellipse. KPF/KRF/KE carve transparent paper from a
# silhouette. Existing operations retain their semantics.
ICONS = {
    # A sheet of paper with a dog-eared corner and three text lines. The plain
    # framed page it used to be was a near-copy of "ebook" (same rectangle, same
    # three rules) — the two apps were indistinguishable in the Applications
    # folder. The folded corner is the universal "document you write" mark and
    # is what now separates the word processor from the reader.
    "writer":     [("M", 5.5, 3), ("L", 14, 3), ("L", 18.5, 7.5), ("L", 18.5, 21), ("L", 5.5, 21), ("L", 5.5, 3),
                   ("M", 14, 3), ("L", 14, 7.5), ("L", 18.5, 7.5),
                   ("M", 8.5, 11.5), ("L", 15.5, 11.5), ("M", 8.5, 15), ("L", 15.5, 15), ("M", 8.5, 18.5), ("L", 12.5, 18.5)],
    "novel":      [("M", 12, 6), ("L", 12, 19.5), ("B", 9, 4.2, 6.5, 4.5, 4.5, 5.5), ("L", 4.5, 19), ("B", 7, 18.2, 9.5, 18.4, 12, 19.5), ("B", 14.5, 18.4, 17, 18.2, 19.5, 19), ("L", 19.5, 5.5), ("B", 17.5, 4.5, 15, 4.2, 12, 6)],
    # A zine page divided into unequal panels. The internal rules make this a
    # comic page, not Writer's dog-eared sheet or Novel's open book.
    "comics":     [("R", 5, 3, 14, 18),
                   ("M", 5, 11), ("L", 19, 11),
                   ("M", 12.5, 3), ("L", 12.5, 11)],
    # A mortarboard. Authored on 2.5-21.5 it was the widest glyph in the set and
    # rendered ~20% larger and heavier than everything beside it in the
    # Applications grid; the same shape now sits in the 4-20 optical box the
    # other app icons share.
    "academic":   [("M", 4, 9.6), ("L", 12, 5.5), ("L", 20, 9.6), ("L", 12, 13.7), ("L", 4, 9.6), ("M", 7.4, 11.3), ("L", 7.4, 16.4), ("M", 16.6, 11.3), ("L", 16.6, 16.4)],
    "journal":    [("RR", 5, 3.5, 14, 17, 1.5), ("M", 8.5, 3.5), ("L", 8.5, 20.5), ("M", 6.5, 7), ("L", 8.5, 7), ("M", 6.5, 11), ("L", 8.5, 11), ("M", 6.5, 15), ("L", 8.5, 15)],
    "screenplay": [("RR", 3.5, 7.5, 17, 12.5, 1.5), ("M", 3.8, 11), ("L", 20.2, 11), ("M", 6, 7.5), ("L", 7.2, 4.5), ("M", 11, 7.5), ("L", 12.2, 4.5), ("M", 16, 7.5), ("L", 17.2, 4.5)],
    "tasks":      [("M", 5, 4.5), ("L", 5, 19.5), ("M", 8.5, 6.5), ("L", 19, 6.5), ("M", 8.5, 12), ("L", 19, 12), ("M", 8.5, 17.5), ("L", 15, 17.5), ("M", 3.5, 11.8), ("L", 5.2, 13.5), ("L", 8, 10.2)],
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
    "calculator": [("RR", 5, 3, 14, 18, 1.8), ("RR", 7.5, 5.5, 9, 3.5, 0.7), ("M", 8, 12.5), ("L", 10, 12.5), ("M", 14, 12.5), ("L", 16, 12.5), ("M", 9, 11.5), ("L", 9, 13.5), ("M", 15, 11.5), ("L", 15, 13.5), ("M", 8, 17), ("L", 10, 17), ("M", 14, 17), ("L", 16, 17)],
    "accounting": [("RR", 3, 7, 18, 10, 1.5), ("M", 3.5, 9.5), ("B", 7, 8.5, 8.5, 10, 12, 10), ("B", 15.5, 10, 17, 8.5, 20.5, 9.5), ("M", 3.5, 14.5), ("B", 7, 15.5, 8.5, 14, 12, 14), ("B", 15.5, 14, 17, 15.5, 20.5, 14.5), ("C", 12, 12, 1.5)],
    # A stamped envelope. The Bill Tracker is the app for paying a bill by post
    # or by phone, and the envelope is what a person actually holds while doing
    # the first of those. Deliberately NOT a document with a total on it: at
    # 22px in the Finder list that is indistinguishable from "writer" (a sheet
    # with three rules), and NOT a tray, which is "inbox". The flap is drawn as
    # a shallow V rather than as the two full diagonals of the closed envelope,
    # so the stamp in the top-right corner still has clear paper to sit on.
    # Envelope FRONT, not back: body, the stamp inside the top-right corner,
    # one address rule. A stamp and a flap-V never coexist on one real face —
    # the old glyph mixed them and the stamp floated above the body like a
    # folder tab (test-batch redesign, rendered at 22px and 128px).
    "bills":      [("R", 3, 6, 18, 12),
                   ("R", 15.8, 8.4, 3.6, 3.2),
                   ("M", 6, 14.6), ("L", 13, 14.6)],
    "contacts":   [("C", 12, 8.5, 3.2), ("M", 5.5, 19), ("L", 5.5, 17), ("L", 18.5, 17), ("L", 18.5, 19)],
    "messages":   [("R", 4, 5, 16, 10), ("M", 9, 15), ("L", 9, 19), ("L", 13, 15), ("C", 9, 10, 0.6), ("C", 12, 10, 0.6), ("C", 15, 10, 0.6)],
    "g2048":      [("RR", 4, 4, 7, 7, 1.3), ("RR", 13, 4, 7, 7, 1.3), ("RR", 4, 13, 7, 7, 1.3), ("RR", 13, 13, 7, 7, 1.3), ("M", 7.5, 6), ("L", 7.5, 9), ("M", 15, 7.5), ("L", 18, 7.5), ("M", 7.5, 15), ("L", 7.5, 18), ("M", 15, 16.5), ("L", 18, 16.5)],
    "tetris":     [("RR", 4, 5, 5, 5, 0.8), ("RR", 9.5, 5, 5, 5, 0.8), ("RR", 15, 5, 5, 5, 0.8), ("RR", 9.5, 10.5, 5, 5, 0.8)],
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
    "sequencer":  [("M", 4, 7), ("L", 20, 7), ("M", 4, 12), ("L", 20, 12), ("M", 4, 17), ("L", 20, 17), ("M", 7.5, 5), ("L", 7.5, 9), ("M", 13, 10), ("L", 13, 14), ("M", 17.5, 15), ("L", 17.5, 19)],
    "composer":   [("M", 4, 7), ("L", 20, 7), ("M", 4, 12), ("L", 20, 12), ("M", 4, 17), ("L", 20, 17), ("RR", 6.5, 5, 5.5, 4, 1), ("RR", 12.5, 10, 6, 4, 1), ("RR", 5, 15, 5, 4, 1)],
    "video":      [("RR", 3.5, 6.5, 12.5, 11, 1.8), ("M", 16, 10), ("L", 20.5, 7.8), ("L", 20.5, 16.2), ("L", 16, 14)],
    "media":      [("RR", 3, 5, 18, 14, 1.8), ("C", 9, 10, 1.5), ("M", 4.5, 17.5), ("L", 9, 13), ("L", 12, 15.5), ("L", 15.5, 12.5), ("L", 20, 17)],
    "music":      [("M", 8.5, 17.5), ("L", 8.5, 7), ("L", 18.5, 4.5), ("L", 18.5, 15.5), ("M", 8.5, 9.5), ("L", 18.5, 7), ("C", 6, 18, 2.5), ("C", 16, 16, 2.5)],
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
    # USB Writer: the stick itself, stood on end — body, metal plug, and the
    # two contacts in it. NOT a box with an arrow going in: that is already the
    # installer's mark, and at 16px the two would have been the same row twice.
    "usbwriter":  [("R", 7.5, 8.5, 9, 13), ("R", 10, 3.5, 4, 5),
                   ("M", 10.8, 5), ("L", 13.2, 5),
                   ("M", 10.8, 6.8), ("L", 13.2, 6.8),
                   ("M", 7.5, 12.5), ("L", 16.5, 12.5)],

    # ---- subject marks ----------------------------------------------------
    # These name a THING, not an app: a cup, a clock, a heart. The Language
    # course tree needs forty distinguishable skill marks, and reusing app
    # glyphs for them produced a tree where Family wore the Contacts icon and
    # Clothing wore a cardboard box. Nothing outside ICONS resolves to these,
    # so they are safe from icon_uniqueness_selftest's app-glyph rules and
    # available to any app that wants a subject mark.
    "cup":        [("M", 6.8, 7), ("L", 8.4, 20.5), ("L", 15.6, 20.5), ("L", 17.2, 7), ("L", 6.8, 7),
                   ("M", 17.1, 8.8), ("Q", 21.2, 12, 16.4, 15.4),
                   ("M", 7.4, 11.2), ("L", 16.6, 11.2)],
    "palette":    [("C", 12, 12, 8.6), ("A", 8.4, 9.2, 1.1), ("A", 12, 7.6, 1.1),
                   ("A", 15.6, 9.6, 1.1), ("C", 13.4, 15.6, 2.0)],
    # An adult and a child, both drawn as whole figures. Two head-and-shoulders
    # marks side by side (the "contacts" construction, twice) read as two
    # horizontal bars at 24px, not as two people.
    "family":     [("C", 7.6, 6.2, 2.8), ("M", 7.6, 9), ("L", 7.6, 15.4),
                   ("M", 3.4, 11.6), ("L", 11.8, 11.6),
                   ("M", 7.6, 15.4), ("L", 4.6, 21), ("M", 7.6, 15.4), ("L", 10.6, 21),
                   ("C", 17.4, 11.2, 2.1), ("M", 17.4, 13.3), ("L", 17.4, 17.4),
                   ("M", 14.2, 15), ("L", 20.6, 15),
                   ("M", 17.4, 17.4), ("L", 15.4, 21), ("M", 17.4, 17.4), ("L", 19.4, 21)],
    "bolt":       [("M", 13.6, 2.8), ("L", 6.4, 13.6), ("L", 11.6, 13.6), ("L", 10.4, 21.2),
                   ("L", 17.6, 10.4), ("L", 12.4, 10.4), ("L", 13.6, 2.8)],
    "question":   [("M", 8.6, 8.8), ("Q", 8.6, 4.4, 12, 4.4), ("Q", 15.6, 4.4, 15.6, 8.6),
                   ("Q", 15.6, 11.8, 12, 13.2), ("L", 12, 15.8), ("A", 12, 19.4, 1.1)],
    "nosign":     [("C", 12, 12, 8.6), ("M", 5.9, 5.9), ("L", 18.1, 18.1)],
    "shirt":      [("M", 8.6, 3.4), ("L", 4.8, 5.6), ("L", 3.2, 9.6), ("L", 6.4, 11),
                   ("L", 6.4, 20.6), ("L", 17.6, 20.6), ("L", 17.6, 11), ("L", 20.8, 9.6),
                   ("L", 19.2, 5.6), ("L", 15.4, 3.4), ("Q", 12, 7.2, 8.6, 3.4)],
    "paw":        [("A", 6.6, 10.4, 1.9), ("A", 10.5, 7.4, 1.9), ("A", 15.1, 7.6, 1.9),
                   ("A", 18.6, 11, 1.9), ("C", 12.2, 17.2, 4.2)],
    # Base bottom-left, tip top-right, one edge bulging each way, midrib along
    # the diagonal. Symmetric control points drew an almond that read as an eye.
    "leaf":       [("M", 4.2, 19.8), ("Q", 3.6, 7, 19.8, 4.2), ("Q", 17, 20.4, 4.2, 19.8),
                   ("M", 4.2, 19.8), ("L", 16.2, 7.8)],
    "clock":      [("C", 12, 12, 8.6), ("M", 12, 6.4), ("L", 12, 12.2), ("L", 15.9, 14.6)],
    # Control points pushed well outside the outline: a quadratic whose control
    # sits near the curve draws almost a straight line, which is why the first
    # attempt at this came out a box with one bump on it.
    # Control points are pushed HALF AGAIN past where the shape wants them,
    # because ("Q", ...) is a cubic with both control points stacked on one
    # spot, which bulges noticeably less than the quadratic it is standing in
    # for. Drawn to the nominal control points, this came out a flat-sided box.
    "cloud":      [("M", 7.0, 18.4), ("Q", 1.0, 18.4, 2.6, 13.0), ("Q", 3.6, 8.4, 8.0, 10.2),
                   ("Q", 7.4, 2.6, 13.4, 5.4), ("Q", 18.4, 3.0, 17.6, 10.4),
                   ("Q", 23.6, 9.4, 22.2, 14.6), ("Q", 22.4, 18.4, 17.2, 18.4),
                   ("L", 7.0, 18.4)],
    "compass":    [("C", 12, 12, 8.6), ("M", 15.8, 8.2), ("L", 10.3, 10.3), ("L", 8.2, 15.8),
                   ("L", 13.7, 13.7), ("L", 15.8, 8.2)],
    "bus":        [("R", 3.4, 4.6, 17.2, 11.6), ("M", 3.4, 10.4), ("L", 20.6, 10.4),
                   ("M", 8, 4.6), ("L", 8, 10.4), ("M", 16, 4.6), ("L", 16, 10.4),
                   ("C", 7.4, 18.6, 1.7), ("C", 16.6, 18.6, 1.7)],
    "plane":      [("M", 21.2, 3.4), ("L", 2.8, 11.4), ("L", 10.2, 13.8), ("L", 12.6, 21.2),
                   ("L", 21.2, 3.4), ("M", 10.2, 13.8), ("L", 21.2, 3.4)],
    "heart":      [("M", 12, 20.4), ("Q", 2.4, 12.6, 4.6, 7.8), ("Q", 7.4, 3.2, 12, 8.6),
                   ("Q", 16.6, 3.2, 19.4, 7.8), ("Q", 21.6, 12.6, 12, 20.4)],
    "body":       [("C", 12, 5.6, 3.0), ("M", 12, 8.6), ("L", 12, 15.2),
                   ("M", 6.2, 11.4), ("L", 17.8, 11.4),
                   ("M", 12, 15.2), ("L", 8, 21.4), ("M", 12, 15.2), ("L", 16, 21.4)],
    "cross":      [("M", 9.4, 3.4), ("L", 14.6, 3.4), ("L", 14.6, 9.4), ("L", 20.6, 9.4),
                   ("L", 20.6, 14.6), ("L", 14.6, 14.6), ("L", 14.6, 20.6), ("L", 9.4, 20.6),
                   ("L", 9.4, 14.6), ("L", 3.4, 14.6), ("L", 3.4, 9.4), ("L", 9.4, 9.4),
                   ("L", 9.4, 3.4)],
    "briefcase":  [("R", 2.8, 7.4, 18.4, 12.8), ("M", 8.6, 7.4), ("L", 8.6, 4.6),
                   ("L", 15.4, 4.6), ("L", 15.4, 7.4), ("M", 2.8, 13), ("L", 21.2, 13)],
    "coins":      [("C", 12, 12, 8.6), ("M", 12, 6.2), ("L", 12, 17.8),
                   ("M", 15, 9.2), ("Q", 8.8, 7.4, 8.8, 10.6), ("Q", 8.8, 13.2, 15.2, 13.4),
                   ("Q", 15.2, 16.6, 9, 14.8)],
    "cart":       [("M", 2.4, 4.6), ("L", 5.6, 4.6), ("L", 8.2, 15.6), ("L", 18.4, 15.6),
                   ("L", 20.8, 7.8), ("L", 6.3, 7.8),
                   ("C", 9, 19.2, 1.5), ("C", 17.2, 19.2, 1.5)],
    "ball":       [("C", 12, 12, 8.6), ("M", 12, 6.2), ("L", 16.7, 9.6), ("L", 14.9, 15.2),
                   ("L", 9.1, 15.2), ("L", 7.3, 9.6), ("L", 12, 6.2)],
    # A conifer, not a broadleaf: a round canopy on a stem reads as a balloon
    # at the size a skill node draws it, the tiers do not.
    "tree":       [("M", 12, 2.8), ("L", 6.6, 10.6), ("L", 9.4, 10.6), ("L", 4.8, 17.2),
                   ("L", 19.2, 17.2), ("L", 14.6, 10.6), ("L", 17.4, 10.6), ("L", 12, 2.8),
                   ("M", 12, 17.2), ("L", 12, 21.4)],
    "city":       [("R", 2.8, 10, 6, 11.2), ("R", 9.4, 4.6, 6, 16.6), ("R", 16, 12.4, 5.2, 8.8),
                   ("M", 11.2, 8.2), ("L", 13.6, 8.2), ("M", 11.2, 12), ("L", 13.6, 12),
                   ("M", 4.8, 13.6), ("L", 6.8, 13.6)],
    "flame":      [("M", 12, 21.2), ("Q", 4.8, 17.4, 7.4, 10.8), ("Q", 9, 7.2, 12, 2.8),
                   ("Q", 15.6, 8.4, 17, 10.8), ("Q", 19.6, 17.4, 12, 21.2),
                   ("M", 12, 21.2), ("Q", 8.8, 18, 10.5, 14.4), ("Q", 12, 12.4, 13.5, 14.8),
                   ("Q", 15.2, 18, 12, 21.2)],
    "crown":      [("M", 3.4, 17.4), ("L", 5.4, 6.8), ("L", 9.5, 11.6), ("L", 12, 4.8),
                   ("L", 14.5, 11.6), ("L", 18.6, 6.8), ("L", 20.6, 17.4), ("L", 3.4, 17.4),
                   ("M", 3.4, 20.2), ("L", 20.6, 20.2)],
    "lock":       [("R", 4.8, 10.4, 14.4, 10.2), ("M", 8.2, 10.4), ("L", 8.2, 7.6),
                   ("Q", 8.2, 3.8, 12, 3.8), ("Q", 15.8, 3.8, 15.8, 7.6), ("L", 15.8, 10.4),
                   ("A", 12, 15.4, 1.2)],
    "trophy":     [("M", 7.8, 3.6), ("L", 16.2, 3.6), ("L", 15.8, 11), ("Q", 12, 14.6, 8.2, 11),
                   ("L", 7.8, 3.6), ("M", 7.9, 5.8), ("L", 4.2, 5.8),
                   ("Q", 4.2, 10.6, 8.5, 11.4), ("M", 16.1, 5.8), ("L", 19.8, 5.8),
                   ("Q", 19.8, 10.6, 15.5, 11.4), ("M", 12, 14.2), ("L", 12, 17.4),
                   ("M", 8.4, 20.6), ("L", 15.6, 20.6), ("L", 14.6, 17.4), ("L", 9.4, 17.4),
                   ("L", 8.4, 20.6)],
    "target":     [("C", 12, 12, 8.6), ("C", 12, 12, 5.2), ("A", 12, 12, 1.8)],
    "speech":     [("M", 4.4, 4.6), ("L", 19.6, 4.6), ("L", 19.6, 15), ("L", 11, 15),
                   ("L", 6.6, 19.6), ("L", 6.6, 15), ("L", 4.4, 15), ("L", 4.4, 4.6),
                   ("A", 9, 9.8, 0.9), ("A", 12, 9.8, 0.9), ("A", 15, 9.8, 0.9)],
}

# Mid-century replacement layer.  Closed legacy outlines become solid poster
# shapes, framed circles become substantial discs, and open gestures receive a
# heavier print-pictogram rule in draw().  Named overrides are purpose-built
# silhouettes where an automatic closure would lose the object's identity.
_MC = {
    "trash": [("RRF", 6, 7.5, 12, 13, 1.2), ("RF", 4.5, 4.5, 15, 2.2),
              ("RRF", 9, 2.8, 6, 2.2, 1), ("KRF", 8, 9.2, 8, 1.6)],
    "music": [("PF", 7.5, 6.5, 19, 3.8, 19, 7, 7.5, 9.7),
              ("RF", 7.5, 7, 2.8, 10.5), ("RF", 16.2, 5, 2.8, 10.5),
              ("E", 4.2, 15.2, 6.6, 4.5, -0.35),
              ("E", 12.9, 13.2, 6.6, 4.5, -0.35)],
    "folder": [("PF", 3, 7, 8.5, 7, 10.5, 9, 21, 9, 20, 20, 3, 20),
               ("PF", 3, 10.5, 21, 9.5, 20, 19, 3, 20),
               ("KPF", 3.5, 10.3, 20.5, 9.4, 20.4, 10.6, 3.5, 11.4)],
    "writer": [("PF", 5, 3, 14, 3, 19, 8, 19, 21, 5, 21),
               ("PF", 14, 3, 19, 8, 14, 8),
               ("KRF", 8, 11, 8, 1.8), ("KRF", 8, 15, 7, 1.8)],
    "contacts": [("A", 12, 8, 4), ("PF", 4, 20, 5.5, 15, 9, 12, 15, 12, 18.5, 15, 20, 20)],
    "terminal": [("RRF", 3, 5, 18, 14, 2),
                 ("KPF", 7, 9, 11, 12, 7, 15, 5.5, 13.5, 8, 12, 5.5, 10.5),
                 ("KRF", 12, 14, 5, 1.8)],
    "calculator": [("RRF", 5, 3, 14, 18, 2), ("KRF", 7.5, 5.5, 9, 3.5),
                   ("KRF", 8, 12, 3, 2), ("KRF", 13, 12, 3, 2),
                   ("KRF", 8, 16, 3, 2), ("KRF", 13, 16, 3, 2)],
    "calendar": [("RRF", 4, 5, 16, 15, 2), ("KRF", 6, 10, 12, 7),
                 ("RF", 4, 8, 16, 2), ("RRF", 7, 3, 3, 5, 1), ("RRF", 14, 3, 3, 5, 1)],
    "bills": [("RRF", 3, 6, 18, 12, 2), ("KPF", 4, 8, 12, 14, 20, 8, 20, 10, 12, 16, 4, 10)],
    "ebook": [("RRF", 5, 3, 14, 18, 2), ("KRF", 8, 7, 8, 1.6),
              ("KRF", 8, 11, 8, 1.6), ("KRF", 8, 15, 5, 1.6), ("KA", 12, 19, 1)],
    "journal": [("RRF", 5, 3, 14, 18, 2), ("KRF", 8, 3, 1.7, 18),
                ("KRF", 11, 7, 5, 1.5), ("KRF", 11, 11, 5, 1.5)],
    "screenplay": [("RRF", 3, 7, 18, 13, 2), ("KRF", 5, 12, 14, 5),
                   ("PF", 3, 7, 21, 7, 21, 10, 3, 12)],
    "media": [("RRF", 3, 5, 18, 14, 2), ("KPF", 5, 16, 9, 11, 12, 14, 15, 10, 19, 16),
              ("KA", 8, 9, 1.5)],
    "mealplanner": [("RRF", 3, 5, 18, 15, 2), ("KRF", 5, 10, 14, 7),
                    ("A", 12, 13.5, 2.5), ("RF", 7, 11, 1.5, 5), ("RF", 16, 11, 1.5, 5)],
    "g2048": [("RRF", 3, 3, 18, 18, 2), ("KRF", 11, 3, 2, 18),
              ("KRF", 3, 11, 18, 2), ("RF", 11, 10, 2, 4), ("RF", 10, 11, 4, 2)],
    "composer": [("RRF", 3, 5, 18, 14, 2), ("KRF", 5, 8, 14, 1.5),
                 ("KRF", 5, 12, 14, 1.5), ("KRF", 5, 16, 14, 1.5),
                 ("RRF", 6, 6.5, 5, 4, 1), ("RRF", 13, 10.5, 5, 4, 1),
                 ("RRF", 8, 14.5, 5, 4, 1)],
    "pause": [("RRF", 6.5, 4, 4.5, 16, 1), ("RRF", 13, 4, 4.5, 16, 1)],
    "stopsq": [("RRF", 5, 5, 14, 14, 1.8)],
    "play": [("PF", 7, 4, 20, 12, 7, 20)],
    "rew": [("PF", 3, 12, 11.5, 4, 11.5, 20), ("PF", 11.5, 12, 20, 4, 20, 20)],
    "ff": [("PF", 4, 4, 12.5, 12, 4, 20), ("PF", 12.5, 4, 21, 12, 12.5, 20)],
    "wshade": [("RRF", 5, 8, 14, 8, 2), ("RF", 8, 11.2, 8, 1.6)],
    "line": [("PF", 4, 17.5, 17.5, 4, 20, 6.5, 6.5, 20), ("A", 5.2, 18.8, 1.8)],
    "rect": [("RRF", 4, 5, 16, 14, 2), ("KRF", 7, 8, 10, 8)],
    "ellipse": [("E", 3, 6, 18, 12, -0.15), ("KE", 6.5, 8.5, 11, 7, -0.15)],
    "fill": [("PF", 8, 3, 18, 12, 10, 20, 3, 13), ("A", 19, 17, 2.3),
             ("PF", 5, 13, 10, 18, 16, 12, 11, 7)],
    "disk": [("RRF", 3, 6, 18, 12, 3), ("RRF", 6, 9, 9, 3, 1), ("A", 17.5, 14, 1.4)],
    "inbox": [("PF", 4, 6, 20, 6, 22, 19, 2, 19),
              ("PF", 2, 13, 8, 13, 10, 16, 14, 16, 16, 13, 22, 13, 22, 20, 2, 20)],
    "cartridge": [("RRF", 5, 3, 14, 18, 2), ("KRF", 8, 6, 8, 6),
                  ("PF", 8, 21, 8, 17, 10, 17, 10, 21), ("PF", 14, 21, 14, 17, 16, 17, 16, 21)],
    "sources": [("RRF", 3, 4, 18, 7, 2), ("RRF", 3, 13, 18, 7, 2),
                ("A", 6.5, 7.5, 1.2), ("A", 6.5, 16.5, 1.2)],
    "toc": [("RRF", 3, 4, 18, 3, 1), ("RRF", 3, 10.5, 18, 3, 1),
            ("RRF", 3, 17, 12, 3, 1), ("A", 19, 18.5, 1.5)],
    "trfade": [("RRF", 3, 5, 8, 14, 2), ("PF", 12, 5, 21, 8, 21, 16, 12, 19)],
    "trdissolve": [("RRF", 3, 5, 18, 14, 2), ("A", 8, 9, 1.3), ("A", 13, 12, 1.3), ("A", 17, 16, 1.3)],
    "trwipe": [("RRF", 3, 5, 18, 14, 2), ("RF", 11, 5, 2, 14)],
    "trslide": [("RRF", 3, 5, 18, 14, 2), ("PF", 8, 10, 15, 10, 15, 7, 20, 12, 15, 17, 15, 14, 8, 14)],
    "triris": [("RRF", 3, 5, 18, 14, 2), ("A", 12, 12, 4)],
    "trblack": [("RRF", 3, 5, 18, 14, 2), ("PF", 3, 5, 8, 5, 21, 19, 16, 19)],
    "cloud": [("A", 7, 14, 5), ("A", 12, 10, 6), ("A", 17, 14, 5),
              ("RRF", 4, 13, 16, 6, 3)],
}
ICONS.update(_MC)

# Physical objects share the same 1.2-unit softened slab corner. Legacy R/RR
# remain supported primitives; this conversion only affects authored glyphs.
for _icon_name, _icon_ops in tuple(ICONS.items()):
    ICONS[_icon_name] = [
        ("RRF", *op[1:], min(1.2, op[3] / 4, op[4] / 4)) if op[0] == "R" else
        ("RRF", *op[1:]) if op[0] == "RR" else op
        for op in _icon_ops
    ]

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
    # Open accents are screen-print rules, never hairline bodies.
    ctx.set_line_width(max(width, 2.6))
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
        elif k == "RR":
            if started:
                ctx.stroke()
            x, y, w, h, rad = op[1:]
            rad = min(rad, w / 2, h / 2)
            ctx.new_sub_path()
            ctx.arc(x + w - rad, y + rad, rad, -math.pi / 2, 0)
            ctx.arc(x + w - rad, y + h - rad, rad, 0, math.pi / 2)
            ctx.arc(x + rad, y + h - rad, rad, math.pi / 2, math.pi)
            ctx.arc(x + rad, y + rad, rad, math.pi, 3 * math.pi / 2)
            ctx.close_path(); ctx.stroke(); started = False
        elif k == "RF":
            if started:
                ctx.stroke()
            ctx.rectangle(op[1], op[2], op[3], op[4]); ctx.fill(); started = False
        elif k == "RRF":
            if started:
                ctx.stroke()
            x, y, w, h, rad = op[1:]
            rad = min(rad, w / 2, h / 2)
            ctx.new_sub_path()
            ctx.arc(x + w - rad, y + rad, rad, -math.pi / 2, 0)
            ctx.arc(x + w - rad, y + h - rad, rad, 0, math.pi / 2)
            ctx.arc(x + rad, y + h - rad, rad, math.pi / 2, math.pi)
            ctx.arc(x + rad, y + rad, rad, math.pi, 3 * math.pi / 2)
            ctx.close_path(); ctx.fill(); started = False
        elif k == "PF":
            if started:
                ctx.stroke()
            pts = op[1:]
            ctx.move_to(pts[0], pts[1])
            for i in range(2, len(pts), 2):
                ctx.line_to(pts[i], pts[i + 1])
            ctx.close_path(); ctx.fill(); started = False
        elif k in ("KPF", "KRF", "KE"):
            if started:
                ctx.stroke()
            ctx.save(); ctx.set_operator(cairo.OPERATOR_CLEAR)
            if k == "KPF":
                pts = op[1:]
                ctx.move_to(pts[0], pts[1])
                for i in range(2, len(pts), 2):
                    ctx.line_to(pts[i], pts[i + 1])
                ctx.close_path(); ctx.fill()
            elif k == "KRF":
                ctx.rectangle(op[1], op[2], op[3], op[4]); ctx.fill()
            else:
                x, y, w, h, angle = op[1:]
                ctx.translate(x + w / 2, y + h / 2); ctx.rotate(angle)
                ctx.scale(w / 2, h / 2); ctx.arc(0, 0, 1, 0, 2 * math.pi); ctx.fill()
            ctx.restore(); started = False
        elif k == "E":
            if started:
                ctx.stroke()
            x, y, w, h, angle = op[1:]
            ctx.save(); ctx.translate(x + w / 2, y + h / 2); ctx.rotate(angle)
            ctx.scale(w / 2, h / 2); ctx.arc(0, 0, 1, 0, 2 * math.pi); ctx.fill()
            ctx.restore(); started = False
        elif k == "C":
            if started:
                ctx.stroke(); started = False
            ctx.arc(op[1], op[2], op[3], 0, 2 * math.pi); ctx.stroke()
        elif k == "A":
            if started:
                ctx.stroke(); started = False
            ctx.arc(op[1], op[2], op[3], 0, 2 * math.pi); ctx.fill()
        elif k == "KA":
            if started:
                ctx.stroke(); started = False
            ctx.save(); ctx.set_operator(cairo.OPERATOR_CLEAR)
            ctx.arc(op[1], op[2], op[3], 0, 2 * math.pi); ctx.fill()
            ctx.restore()
        elif k == "Q":
            # quadratic-ish via arc approximation: treat as an arc through 3 pts
            ctx.curve_to(op[1], op[2], op[1], op[2], op[3], op[4])
        elif k == "B":
            ctx.curve_to(*op[1:])
        elif k == "AR":
            if started:
                ctx.stroke()
            ctx.new_sub_path()
            ctx.arc(op[1], op[2], op[3], op[4], op[5]); ctx.stroke(); started = False
        elif k == "F":
            ctx.fill(); started = False
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


# ---------------------------------------------------------------- HiDPI path
#
# THE PROBLEM. Every icon in this OS is a VECTOR — a list of drawing ops — so it
# can be rasterized perfectly at any resolution. `pixbuf()` above throws that
# away: it rasterizes into a `size x size` bitmap in LOGICAL pixels and hands it
# to Gtk.Image. On a panel running at scale 2 (see opt/notebook/display.sh) GTK
# then draws that bitmap into a context scaled by 2, so a 24px icon is smeared
# across 48 device pixels by the interpolator. The result is that on exactly the
# machines bought for their screen, every icon in the interface is soft — while
# the text beside it is sharp, which makes it look worse, not better, than the
# same icon on a normal panel.
#
# A GdkPixbuf cannot fix this: it is a bag of pixels with no notion of scale.
# A cairo SURFACE can — `set_device_scale(n, n)` marks it as "these pixels are n
# per logical unit", and Gtk.Image.new_from_surface honours it, drawing the icon
# at logical `size` using all n*size real pixels.
#
# So: render at size*scale, tell the surface what it is, and let GTK place it.
# The icon is then drawn from the vector at full panel resolution.

_SCALE = None


def scale_factor():
    """The integer device scale the interface is drawing at (1, 2 or 3).

    Asked of GDK first, because GDK is what will actually place the surface and
    is the only thing that knows what a window ended up on. GDK_SCALE is
    consulted as well and the LARGER wins: in an offscreen render — which is how
    every visual check in tools/ works — there is no monitor to ask and GDK
    reports 1, so trusting it alone would make the HiDPI path untestable from
    the harness that exists to test it."""
    global _SCALE
    if _SCALE is not None:
        return _SCALE
    scale = 1
    try:
        disp = Gdk.Display.get_default()
        if disp is not None:
            mon = None
            try:
                mon = disp.get_primary_monitor()
            except Exception:                                     # noqa: BLE001
                mon = None
            if mon is None:
                try:
                    mon = disp.get_monitor(0)
                except Exception:                                 # noqa: BLE001
                    mon = None
            if mon is not None:
                scale = max(scale, int(mon.get_scale_factor() or 1))
    except Exception:                                             # noqa: BLE001
        pass
    try:
        env = (os.environ.get("GDK_SCALE") or "").strip()
        if env.isdigit():
            scale = max(scale, int(env))
    except Exception:                                             # noqa: BLE001
        pass
    _SCALE = max(1, min(3, scale))
    return _SCALE


# The GType to give a Gtk.ListStore column that holds one of these surfaces.
#
# A cairo surface is a BOXED type, not a GObject, so a column declared
# GObject.TYPE_OBJECT (which is what the Finder's model used for its two icon
# columns) will not accept one, and `Gtk.ListStore(cairo.Surface, ...)` is
# rejected outright by PyGObject with "Must be GObject.GType, not type". The
# usable name is registered by the cairo bridge as "CairoSurface"; paired with
# Gtk.CellRendererPixbuf's `surface` property (GTK 3.10+, which honours a
# surface's device scale) it is what lets a TreeView or IconView show a HiDPI
# icon at all. Verified end to end at scale 2 before the Finder was changed.
#
# TYPE_PYOBJECT is the fallback: it also stores the surface, but a cell can then
# only be filled through a cell-data function rather than add_attribute.
try:
    SURFACE_GTYPE = GObject.type_from_name("CairoSurface")
except Exception:                                                 # noqa: BLE001
    SURFACE_GTYPE = GObject.TYPE_PYOBJECT


_SURFACE_CACHE = {}


def surface(name, size, color="#1A1916", width=1.6, flip_v=False):
    """cairo surface for `name`, rasterized at the panel's real resolution.

    `flip_v` mirrors the glyph top-to-bottom, which is how an "up" arrow becomes
    a "down" one without a second icon (Packages' sort indicator). It exists
    here rather than at the call site because the pixbuf equivalent — building
    the icon and calling GdkPixbuf.flip() — only works on a pixbuf, and would
    have quietly kept that one arrow on the blurry path.

    Cached on the same key as pixbuf(). A cairo surface handed to several
    Gtk.Images is safe to share: Gtk.Image only ever reads it."""
    scale = scale_factor()
    key = (name, size, color, width, scale, flip_v,
           _is_rtl() and name in _DIRECTIONAL)
    cached = _SURFACE_CACHE.get(key)
    if cached is not None:
        return cached
    dev = max(1, int(round(size * scale)))
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, dev, dev)
    ctx = cairo.Context(surf)
    if flip_v:
        ctx.translate(0, dev)
        ctx.scale(1, -1)
    # Drawn at the DEVICE size, so the 1.6px stroke is scaled with everything
    # else and comes out 1.6 LOGICAL px thick — not a hairline at 2x.
    draw(ctx, name, dev, color, width)
    surf.flush()
    surf.set_device_scale(scale, scale)
    _SURFACE_CACHE[key] = surf
    return surf


def surface_from_pixbuf(pb, scale=None):
    """Wrap a PHOTO (album art, a thumbnail, a video frame) for a HiDPI screen.

    The icons above are vectors and can simply be redrawn at any resolution.
    Raster content cannot: the only thing to do is decode MORE SOURCE PIXELS and
    then tell GTK that those pixels are finer than logical units. So the caller
    scales its pixbuf to size*scale_factor() and hands it here, and this returns
    a surface carrying the device scale, which Gtk.Image places at the original
    logical size — sharp instead of interpolated.

    Returns None if the bridge or the pixbuf is unusable, so every caller can
    fall back to set_from_pixbuf and still show something."""
    if pb is None:
        return None
    try:
        return Gdk.cairo_surface_create_from_pixbuf(
            pb, int(scale or scale_factor()), None)
    except Exception:                                             # noqa: BLE001
        return None


def set_image_pixbuf(img, pb, scale=None):
    """Show an already-device-resolution pixbuf in `img` at its logical size.
    Drop-in for `img.set_from_pixbuf(pb)` on the HiDPI path."""
    surf = surface_from_pixbuf(pb, scale)
    if surf is not None:
        try:
            img.set_from_surface(surf)
            return img
        except Exception:                                         # noqa: BLE001
            pass
    img.set_from_pixbuf(pb)
    return img


def image(name, size, color="#1A1916", width=1.6):
    """A Gtk.Image showing `name`, crisp at whatever scale the panel runs at.

    Drop-in for the `Gtk.Image.new_from_pixbuf(nbicons.pixbuf(...))` this OS used
    everywhere. Falls back to that older path if the cairo bridge is missing, so
    a build without gi's cairo foreign-type support still shows icons rather
    than nothing (that bridge going missing is what once made every DrawingArea
    in the OS blank, so it is worth not depending on absolutely)."""
    try:
        return Gtk.Image.new_from_surface(surface(name, size, color, width))
    except Exception:                                             # noqa: BLE001
        return Gtk.Image.new_from_pixbuf(pixbuf(name, size, color, width))


def set_image(img, name, size, color="#1A1916", width=1.6):
    """Re-point an existing Gtk.Image at `name`. Drop-in for
    `img.set_from_pixbuf(nbicons.pixbuf(...))`."""
    try:
        img.set_from_surface(surface(name, size, color, width))
        return img
    except Exception:                                             # noqa: BLE001
        img.set_from_pixbuf(pixbuf(name, size, color, width))
        return img


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
