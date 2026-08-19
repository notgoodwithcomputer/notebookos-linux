#!/usr/bin/env python3
"""
Notebook OS desktop shell — the panel (system menu bar) and session owner.

This is a NATIVE GTK3 application (real X client, drawn by GTK), not a web view.
It renders the papertone menu bar across the top of the screen as a strut-docked
panel, exactly matching the design language: the snail-logo menu (the brand mark
sits where the dot did), app-name/app-switcher, per-context menus, and the
right-side clock / date cluster.

It reads real system detail straight from the kernel (/proc) — no network
exists on this machine by design.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")            # for GdkWindow.get_xid()
from gi.repository import Gtk, Gdk, GLib, Pango, GdkX11  # noqa: E402,F401

import time
import os
import json

# Raised by the Media Viewer while a video plays edge-to-edge; see
# _poll_video_full. Holds the player's PID so a stale flag cannot strand the
# desktop with no menu bar.
VIDEO_FULL_FLAG = "/tmp/nb-video-fullscreen"


def _process_token(pid):
    """Return the PID/birth-time identity used by Media's ownership flag."""
    try:
        with open("/proc/%s/stat" % pid) as fh:
            tail = fh.read().rsplit(") ", 1)[1].split()
        return "%s %s" % (pid, tail[19])
    except (OSError, IndexError):
        return ""
# subprocess is imported lazily inside the functions that spawn processes
# (launch / _paint_below_bar / _power / _do_power). None of those run during
# construct or the first paint, so the boot-foreground panel never pays the
# subprocess import cost before it is on screen.

import xshape
import nbapp  # for nudge_paint (swrast scanout flush) + version/pretty name
import nbicons        # the bell mark and the per-app glyph on a notification
import nbnotify       # the notification spool every app posts to
import nbprefs        # the one place a saved preference is turned into effect
# Fail CLOSED: nothing launches without a release-key signature over the
# bytes on disk (docs/APP-TRUST.md). A missing nbtrust refuses.
try:
    import nbtrust
except Exception:
    nbtrust = None
# _t: panel menu labels (Finder/File/Edit/View/Label) translate.
# set_verbatim: ...and the one card that reports the CLIPBOARD's own bytes back
# must NOT (see _card_dialog).
from nbi18n import _t, set_verbatim
# The panel is MOTION-EXEMPT: the menu bar stays static across the OS, and its
# dropdowns appear and leave at rest (design owner's direction, 2026-08-10 —
# the G1 drop-from-the-title arrival is retired; see motion_inventory
# system.panel-menu-open/close). Nothing here animates, so no nbmotion.

PANEL_H = 46
# The right cluster's margin — and therefore the vertical line the clock, date
# and battery all end on. A dropdown falling out of that cluster rests its right
# edge here rather than on the bare screen edge, so it lands against structure
# that is already visible (Paper Physics §E2). Named once because the margin and
# the alignment must never be able to disagree.
RIGHT_MARGIN = 20
# The bell mark, in logical px. 17 rather than the toolbar-usual 16 so Lucide's
# unread spot (r=3 on a 24 grid) rasterises as a 4px disc instead of a 3px one:
# below that it stops reading as a deliberate mark and starts reading as a
# stray pixel, which is the one thing an unread signal cannot afford.
BELL_PX = 17
# The dropdown card's corner radius — must match .sysmenu's border-radius in
# the CSS below. The X shape mask is built of rectangles only, so the rounded
# corners are cut as one-pixel rows (menu_shape_rects): everything the arc
# excludes shows the DESKTOP, exactly like the CSS paints it. Without this the
# window's opaque papertone host fill (no alpha — force_opaque_visual) showed
# as solid notches behind the card's rounded corners.
MENU_SHAPE_RADIUS = 12


def menu_shape_rects(rect, radius):
    """A rounded rectangle as X shape rows: a full-width middle band plus
    per-row corner slices whose insets follow the arc. Pure math, so the
    selftest can prove the silhouette without an X server."""
    x, y, w, h = rect
    r = max(0, min(int(radius), w // 2, h // 2))
    if r <= 0:
        return [rect]
    rows = []
    for dy in range(r):
        v = r - dy - 0.5                     # pixel-row centre vs arc centre
        s = (r * r - v * v) ** 0.5
        inset = int(round(r - s))
        rw = w - 2 * inset
        if rw > 0:
            rows.append((x + inset, y + dy, rw, 1))            # top corners
            rows.append((x + inset, y + h - 1 - dy, rw, 1))    # bottom corners
    if h > 2 * r:
        rows.append((x, y + r, w, h - 2 * r))                  # middle band
    return rows
# An open dropdown auto-dismisses after this many seconds of NO interaction —
# a safety net that also reverts the whole-screen input shape if a menu is left
# hanging. Pointer movement over the menu restarts the timer (_menu_activity),
# so a menu never vanishes from under someone who is still reading it.
MENU_IDLE_TIMEOUT_S = 15
# ...but the notification centre is a surface somebody READS, not a list they
# pick from, and fifteen seconds is not long enough to read a full tray. The
# safety net is still a net at forty-five; a card that vanishes mid-sentence is
# a defect, not a safeguard.
NOTIFY_IDLE_TIMEOUT_S = 45
DE_DIR = os.path.dirname(os.path.abspath(__file__))

# session state (the Finder Label choice) survives close/reboot under
# $NB_HOME/.config/notebook, matching every app's persistence pattern.
HOME = os.environ.get("NB_HOME", "/root")
CFG_DIR = os.path.join(HOME, ".config", "notebook")
SHELL_FILE = os.path.join(CFG_DIR, "shell.json")
# The Settings app's own store, watched here for the time zone (see
# _sync_timezone). Settings owns this file; the panel only ever reads it.
SETTINGS_FILE = os.path.join(CFG_DIR, "settings.json")

# Mac OS 7 Finder "Label" menu: name -> colour of its dot (None clears it).
# There is no shared selection between this panel and a separate Finder
# process, so the menu keeps ONE session label (persisted) rather than tagging
# a file; the design system's signage red marks the active/selected label.
# The six label COLOURS. Names are the user's, and start empty: the shipped
# "Essential / Hot / In Progress / ..." were placeholder copy describing an
# imagined filing system rather than this user's, so a fresh install offers six
# unnamed colours and Label > Edit Labels... is where they get their meaning.
# Warm terracotta first — NOT the reserved brand signage red #C8341E, which
# stays exclusive to the active-label checkmark (_label_item_markup).
FINDER_LABEL_COLORS = ["#B5502F", "#CC6B1F", "#B8912E",
                       "#3E6B8C", "#4F7A3A", "#6E695E"]
N_LABELS = len(FINDER_LABEL_COLORS)


def launch(mod, name=""):
    """Start DE module `mod`. `name` is what the person who asked calls it —
    the menu row's own words, or the app name a notification recorded — and is
    used only if the launch is REFUSED, so the message in the tray says which
    app it is about. The caller passes it because the panel must not keep a
    second table of what the apps are (see nbnotify.post)."""
    # A launched app hides the desktop home (Finder + widget column) while it
    # runs. That flag (/tmp/nb-app-active) is now owned by nbapp.AppWindow and
    # ref-counted across every app process — however it was launched, including
    # the session-launched installer — so launching just starts the process.
    # finder/widgets ARE the desktop home and are not AppWindows, so they never
    # set the flag.
    import subprocess
    script = os.path.join(DE_DIR, mod + ".py")
    if os.path.exists(script):
        ok, why = (nbtrust.check_path(script) if nbtrust
                   else (False, "the trust module is missing"))
        if not ok:
            print("nbtrust: refused %s (%s)" % (script, why))
            try:
                import nbnotify
                # No `app`: a row that records the module offers to OPEN it,
                # and opening it is what was just refused — clicking the row
                # took the message away, ran the same refusal again and left
                # an identical message in its place. The icon is passed
                # directly so dropping `app` costs the row nothing, and the
                # sender line names the app the refusal is about, which the
                # shared title cannot.
                nbnotify.post(_t("This app can't be opened on this computer."),
                              app_name=name or _t("System"),
                              icon=nbicons.glyph_for(mod))
            except Exception:
                pass
            return
        subprocess.Popen(["python3", script],
                         env=dict(os.environ, PYTHONPATH=DE_DIR))


def launch_finder():
    launch("finder")


def launch_finder_at(rel):
    """Open a Finder window on a folder. rel is relative to NB_HOME ("" = Home),
    matching finder.py's own PLACES table and its argv[1] convention."""
    import subprocess
    script = os.path.join(DE_DIR, "finder.py")
    if os.path.exists(script):
        subprocess.Popen(["python3", script, rel],
                         env=dict(os.environ, PYTHONPATH=DE_DIR))


def read_first_line(path, default=""):
    try:
        with open(path) as fh:
            return fh.readline().strip()
    except OSError:
        return default


# The bell's two states, cached: rendering is a pure function of (unread, px,
# panel scale) and the tick asks for one of them every time the tray changes.
_BELL_CACHE = {}


def bell_surface(unread, px=BELL_PX):
    """The menu-bar bell, quiet or carrying its unread spot. None if it cannot
    be drawn, so the caller can fall back to a plain icon rather than to a hole.

    Two carriers for one state, per Constitution Article VII §3: the glyph goes
    from the muted register the date and battery sit in up to full ink, AND the
    signage-red spot appears. Neither is colour alone, so the change survives
    both a colour-blind reader and a monochrome panel.

    Built as a cairo surface the way nbicons.surface does it, rather than as a
    Gtk.DrawingArea: a DrawingArea's draw handler needs gi's cairo bridge, whose
    absence once blanked every custom-drawn widget in this OS. This path only
    uses pycairo plus Gtk.Image.new_from_surface, which is what nbicons already
    proves works on the shipped image.
    """
    key = (bool(unread), px, nbicons.scale_factor())
    hit = _BELL_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        import cairo
        import math
        scale = nbicons.scale_factor()
        dev = max(1, int(round(px * scale)))
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, dev, dev)
        ctx = cairo.Context(surf)
        if unread:
            # Fill the notch bell-dot leaves at (18,5) r=3 on the 24 grid, then
            # stroke the glyph over it: the ink outline lands exactly on the
            # fill's edge, so the spot is registered into the drawing instead of
            # sitting on top of it.
            ctx.save()
            ctx.scale(dev / 24.0, dev / 24.0)
            ctx.set_source_rgb(0xC8 / 255.0, 0x34 / 255.0, 0x1E / 255.0)
            ctx.arc(18, 5, 3, 0, math.tau)
            ctx.fill()
            ctx.restore()
        # Drawn at the DEVICE size so the 1.6px stroke stays 1.6 LOGICAL px on
        # a 2x panel — the same reasoning as nbicons.surface.
        nbicons.draw(ctx, "belldot" if unread else "bell", dev,
                     "#1A1916" if unread else "#6E695E")
        surf.flush()
        surf.set_device_scale(scale, scale)
    except Exception:                                             # noqa: BLE001
        return None
    _BELL_CACHE[key] = surf
    return surf


class Panel(Gtk.Window):
    """The top menu bar, docked as a panel with a strut reservation."""

    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        # Style itself rather than relying on main() having done it, so a Panel
        # built by anything else (a render harness, a test) looks like the real
        # one. Idempotent; see install_css below.
        install_css()
        self.set_decorated(False)
        nbapp.force_opaque_visual(self)   # see nbapp: no RGBA visual
        self.set_skip_taskbar_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.set_keep_above(True)
        # A DOCK does not take the keyboard. It never did on this machine --
        # matchbox does not move focus for a click -- but nbapp's dispatcher now
        # gives a clicked window the focus the WM will not (see
        # note_input_modality), and without this the menu bar would take the
        # caret out of whatever app was being typed into every time someone
        # opened a menu. Declaring it here is also simply what a panel is: its
        # menus take a GTK grab of their own and work perfectly without it.
        self.set_accept_focus(False)

        display = Gdk.Display.get_default()
        mon = display.get_primary_monitor() or display.get_monitor(0)
        geo = mon.get_geometry() if mon is not None else None
        if geo is not None and geo.width > 1 and geo.height > 1:
            self.screen_w = geo.width
            self.screen_h = geo.height
        else:
            # Degenerate/absent geometry (early boot, headless probe): fall back
            # to the shared helper rather than sizing the panel to 0. Real panels
            # are NOT assumed to be 1920x1080 — nbapp.screen_size() returns the
            # real primary-monitor pixels (and only falls back sanely on its own).
            self.screen_w, self.screen_h = nbapp.screen_size()
        # The panel window spans the whole screen but an X shape mask clips
        # it to the menu bar — and, while a menu is open, to the bar PLUS the
        # menu rectangle. Dropdowns are drawn INSIDE this window (a Gtk.Fixed
        # overlay). set_size_request keeps GTK allocating the Fixed full
        # height so a menu placed below the bar has room. (See the long
        # comment on _paint_below_bar for the repaint caveat under this
        # no-compositor stack.)
        self.set_size_request(self.screen_w, self.screen_h)
        self.set_default_size(self.screen_w, self.screen_h)
        self.move(0, 0)

        self._menu = None            # the open menu EventBox, if any
        self._menu_for = None        # which bar button opened it
        self._menu_rect = None       # (x, y, w, h) of the open menu
        self._menu_timeout = None
        self._menu_active_at = 0      # monotonic us of the last menu interaction
        self._nudge_sources = []     # GLib timeout ids for pending paint nudges
        self._nudge_pending = False  # an open menu is awaiting its first paint
        # Does the GPU stack (virgl / real hardware) repaint a freshly-mapped
        # menu on its own? session.sh detects this once (from the kernel's
        # "[drm] features: +virgl" line — a DRM render node exists even under
        # pure software rendering, so its presence is NOT a reliable signal) and
        # exports NB_ACCEL. On the software (swrast) path the below-bar nudge/
        # flush mitigation runs; on the accelerated path no xnudge is spawned and
        # no per-menu blocking flush happens. Fall back to the render-node check
        # only if NB_ACCEL is unset (app launched outside the session).
        _accel = os.environ.get("NB_ACCEL")
        if _accel in ("0", "1"):
            self._paint_reliable = (_accel == "1")
        else:
            self._paint_reliable = os.path.exists("/dev/dri/renderD128")
        prefs = self._load_prefs()          # persisted session state (shell.json)
        # Harden against a hand-edited or stale shell.json: a Finder Label that
        # is no longer a known label falls back to None (otherwise its active
        # check would silently vanish and no item would look selected), and the
        # View flags are coerced to real bools rather than trusting the file.
        # Label NAMES are user-editable (Label > Edit Labels...), so the live
        # names come from shell.json and start EMPTY (unnamed colours).
        # Only the names are editable; the six colours are part of the design
        # system and stay fixed.
        self._label_names = self._sane_label_names(prefs.get("label_names"))
        # Selected label is an INDEX, not a name: names are user-supplied and may
        # be blank or duplicated, so they cannot identify a label. An older
        # shell.json stored the name — migrate it once, then forget it.
        idx = prefs.get("finder_label_idx")
        if not (isinstance(idx, int) and 0 <= idx < N_LABELS):
            idx = None
            legacy = prefs.get("finder_label")
            if isinstance(legacy, str) and legacy in self._label_names:
                idx = self._label_names.index(legacy)
        self._label_idx = idx
        self._clock_24h = bool(prefs.get("clock_24h", True))      # View: clock format
        self._clock_seconds = bool(prefs.get("clock_seconds", False))  # View: seconds
        self._show_date = bool(prefs.get("show_date", True))     # View: date in bar

        # Gtk.Fixed lets us place the bar at (0,0) and menus at absolute
        # positions below it, all within this one window.
        self.fixed = Gtk.Fixed()
        self.add(self.fixed)

        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self._root_bar = root
        root.get_style_context().add_class("menubar")
        root.set_size_request(self.screen_w, PANEL_H)
        self.fixed.put(root, 0, 0)

        # --- left cluster: logo, app name, menus ---
        left = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        left.set_margin_start(16)
        root.pack_start(left, False, False, 0)

        logo = self._menu_button("", is_logo=True)
        logo.connect("clicked", self._logo_menu)
        left.pack_start(logo, False, False, 0)

        appname = self._menu_button(_t("Finder"), bold=True)
        appname.connect("clicked", self._app_switcher)
        left.pack_start(appname, False, False, 0)

        menu_handlers = {
            "File": self._file_menu, "Edit": self._edit_menu,
            "View": self._view_menu, "Label": self._label_menu,
        }
        for label in ("File", "Edit", "View", "Label"):
            b = self._menu_button(_t(label))
            b.connect("clicked", menu_handlers[label])
            left.pack_start(b, False, False, 0)

        # --- right cluster: notifications, clock, date, battery ---
        right = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        right.set_margin_end(RIGHT_MARGIN)
        root.pack_end(right, False, False, 0)

        # Battery % sits after the date (rightmost). pack_end stacks right-first,
        # so it is packed BEFORE the date. Hidden on machines with no battery
        # (desktops / VMs); _tick shows it only when a Battery supply is found.
        # Every label in this cluster is RIGHT-aligned inside a width reserved
        # for its widest possible reading (see _pin_widths). Packed end-first,
        # a label that grows pushes its neighbours left, so without this the
        # whole cluster twitched sideways every time the minute rolled over, the
        # battery crossed 9%/99%, or a 12-hour clock passed 9:59.
        self.batlbl = Gtk.Label(xalign=1.0)
        self.batlbl.get_style_context().add_class("date")
        self.batlbl.set_no_show_all(True)
        self.batlbl.set_visible(False)
        right.pack_end(self.batlbl, False, False, 0)

        self.datelbl = Gtk.Label(xalign=1.0)
        self.datelbl.get_style_context().add_class("date")
        self.datelbl.set_no_show_all(True)     # View "Show Date" governs this,
        self.datelbl.set_visible(self._show_date)  # not panel.show_all()
        right.pack_end(self.datelbl, False, False, 0)

        self.clocklbl = Gtk.Label(xalign=1.0)
        self.clocklbl.get_style_context().add_class("clock")
        right.pack_end(self.clocklbl, False, False, 0)

        # The notification centre, immediately left of the clock: what the
        # machine has to say sits beside when it said it. Packed LAST of the
        # right cluster, which — this box being packed end-first — puts it
        # leftmost, and means nothing to its right ever moves.
        #
        # It is a menu title like File or View, not an indicator: it takes the
        # same .menuitem chrome, hovers with the same selection swatch, and
        # opens a dropdown drawn inside this window like every other one.
        self.bell = Gtk.Button()
        self.bell.set_relief(Gtk.ReliefStyle.NONE)
        self.bell.get_style_context().add_class("menuitem")
        self.bell.get_style_context().add_class("bell")
        self.bellimg = Gtk.Image()
        self.bell.add(self.bellimg)
        self.bell.connect("clicked", self._notify_menu)
        right.pack_end(self.bell, False, False, 0)

        # last-shown strings, so the per-second tick only repaints a label when
        # its text actually changed (see _tick) — every repaint is CPU-drawn
        # pixels on this GPU-less software-rendered stack.
        self._last_clock = self._last_tip = self._last_date = None
        self._last_bat = None
        self._last_bat_tip = None
        # Notification state, cached the same way and for the same reason. The
        # spool's key is a stat rather than a read (nbnotify.state_key), so the
        # tick only opens a record on the seconds when something changed.
        self._notify_state = None
        self._notify_error = ""
        self._bell_unread = None      # what the mark currently SHOWS
        self._bell_count = None       # ...and what its tooltip currently says
        # Time zone, followed the same cheap way (see _sync_timezone).
        self._tz_stamp = None
        self._paint_bell(nbnotify.unread_count())
        self._pin_widths()
        self._tick()
        GLib.timeout_add_seconds(1, self._tick)
        # ...and the fullscreen-video watch on its own second. It is a
        # SEPARATE source rather than a line in _tick because _tick is also
        # called directly during construct (and after a View toggle), and this
        # one shows/hides the panel window — work that must only ever happen
        # from the main loop, i.e. after main() has shown the bar.
        GLib.timeout_add_seconds(1, self._poll_video_full)

        # dismiss an open menu when the pointer clicks outside it
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("button-press-event", self._maybe_dismiss)
        self.connect("realize", self._on_realize)
        # once the panel is actually on screen, tell the boot splash the desktop
        # is up so it fills to 100% and dismisses. See splash.py / session.sh.
        self.connect("map-event", self._signal_ready)
        self._geometry_refresh_id = 0
        screen = Gdk.Screen.get_default()
        if screen is not None:
            try:
                screen.connect("size-changed", self._queue_geometry_refresh)
                screen.connect("monitors-changed", self._queue_geometry_refresh)
            except Exception:
                pass

    def _signal_ready(self, *_):
        try:
            open("/tmp/nb-ready", "w").close()
        except Exception:
            pass
        return False

    def _queue_geometry_refresh(self, *_):
        """Coalesce the burst of RandR signals produced by one mode change."""
        if not self._geometry_refresh_id:
            self._geometry_refresh_id = GLib.timeout_add(
                100, self._refresh_geometry)
        return False

    def _refresh_geometry(self):
        self._geometry_refresh_id = 0
        display = Gdk.Display.get_default()
        mon = (display.get_primary_monitor() or display.get_monitor(0)) \
            if display is not None else None
        geo = mon.get_geometry() if mon is not None else None
        if geo is not None and geo.width > 1 and geo.height > 1:
            width, height = geo.width, geo.height
        else:
            width, height = nbapp.screen_size()
        if width == self.screen_w and height == self.screen_h:
            return False

        # A dropdown's placement and full-screen input catch were calculated
        # in the old coordinate space. Close it rather than leaving an
        # invisible input-stealing rectangle after a live RandR change.
        self._menu_close()
        self.screen_w, self.screen_h = width, height
        self.set_size_request(width, height)
        self.set_default_size(width, height)
        self._root_bar.set_size_request(width, PANEL_H)
        win = self.get_window()
        if win is not None:
            win.move_resize(0, 0, width, height)
        self._reserve_strut()
        self._apply_shape()
        self.queue_resize()
        return False

    # ---- video fullscreen ----
    def _poll_video_full(self):
        """Stand down while a video is playing edge-to-edge.

        This bar is a DOCK with keep-above, so it paints OVER a fullscreen app
        window — which meant the Media Viewer's fullscreen was fullscreen
        everywhere except the top 46px, where the menu bar sat across the
        picture. The player raises a flag; we get out of the way.

        The flag carries the player's PID and we check it is still alive, so a
        player that dies mid-film cannot leave the machine with no menu bar."""
        want_hidden = False
        try:
            with open(VIDEO_FULL_FLAG) as fh:
                owner = fh.read().strip()
            pid = owner.split(" ", 1)[0]
            want_hidden = bool(owner) and owner == _process_token(pid)
            if not want_hidden:
                os.remove(VIDEO_FULL_FLAG)     # stale: its owner is gone
        except (OSError, ValueError):
            want_hidden = False
        if want_hidden and self.get_visible():
            self.hide()
        elif not want_hidden and not self.get_visible():
            self.show()
            self._reserve_strut()
            self._apply_shape()
        return True

    # ---- shape mask ----
    def _on_realize(self, *_):
        # force full-screen coverage (matchbox may otherwise clamp a DOCK to
        # its strut height); the shape clips what's actually drawn to the bar.
        win = self.get_window()
        if win is not None:
            win.move_resize(0, 0, self.screen_w, self.screen_h)
        self._reserve_strut()
        self._apply_shape()

    def _apply_shape(self):
        """Clip the full-screen window to the bar + open menu, so the desktop
        shows through the rest. ctypes, not Gdk.shape_combine_region — see
        xshape.py for why."""
        win = self.get_window()
        if win is None:
            return
        xid = win.get_xid()
        bar = (0, 0, self.screen_w, PANEL_H)
        # bounding shape: only the bar (+ open menu) is drawn on screen. The
        # menu is clipped to its ROUNDED silhouette, not its rectangle, so
        # the desktop shows through behind the card's rounded corners.
        visible = [bar]
        if self._menu_rect:
            visible += menu_shape_rects(self._menu_rect, MENU_SHAPE_RADIUS)
        xshape.combine(xid, visible, xshape.SHAPE_BOUNDING)
        # input shape: while a menu is open, take the whole screen so a click
        # outside the menu dismisses it; otherwise only the bar, so clicks
        # below pass through to the desktop and apps.
        catch = [(0, 0, self.screen_w, self.screen_h)] if self._menu_rect \
            else [bar]
        xshape.combine(xid, catch, xshape.SHAPE_INPUT)

    def _paint_below_bar(self):
        # Best-effort repaint of the menu when it opens. Under this stack
        # (matchbox + kms_swrast, no compositor) GTK often does not repaint
        # the panel window below the bar — the bar and clock keep redrawing,
        # but a menu placed lower can stay blank. A one-pixel resize from a
        # separate process (xnudge.py) usually forces a full redraw without
        # disturbing the bar. This is only ever reached on the software
        # (swrast) path — under virgl / real hardware the first repaint lands
        # immediately, so no nudge is scheduled (see _popup). Best-effort: it
        # never breaks the bar, but the menu may occasionally not appear under
        # heavy emulation.
        win = self.get_window()
        if win is not None and self._menu_rect is not None:
            # a same-spot pointer warp + flush wakes the X block handler and
            # scans out the freshly-drawn menu region (reliable); keep the
            # xnudge 1px-resize as an extra kick.
            import subprocess
            nbapp.nudge_paint()
            subprocess.Popen(["python3", os.path.join(DE_DIR, "xnudge.py"),
                              hex(win.get_xid()), str(self.screen_w),
                              str(self.screen_h)])
        return False

    def _cancel_nudges(self):
        # drop every pending paint-nudge timeout so a closed/replaced menu's
        # nudges never fire against whatever menu is open next.
        for sid in self._nudge_sources:
            GLib.source_remove(sid)
        self._nudge_sources = []
        self._nudge_pending = False

    def _menu_drawn(self, _w, _cr):
        # the menu actually painted — no coaxing needed; stop the pending nudge.
        self._nudge_pending = False
        return False

    def _nudge_once(self):
        # single-shot: force ONE repaint only if the menu still hasn't painted.
        self._nudge_sources = []         # this one-shot source has now fired
        if self._nudge_pending and self._menu_rect is not None:
            self._paint_below_bar()
        self._nudge_pending = False
        return False

    def _menu_button(self, label, bold=False, is_logo=False):
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.get_style_context().add_class("menuitem")
        if is_logo:
            btn.get_style_context().add_class("logo")
            menu_name = "%s — %s" % (_t("Notebook OS"), _t("Show Menu"))
            btn.set_tooltip_text(menu_name)
            btn.get_accessible().set_name(menu_name)
            img = Gtk.Image()
            try:
                from gi.repository import GdkPixbuf
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    "/opt/notebook/logo.png", 34, 16, True)
                img.set_from_pixbuf(pb)
            except Exception:
                pass
            btn.add(img)
        else:
            lbl = Gtk.Label(label=label)
            if bold:
                lbl.get_style_context().add_class("bold")
            btn.add(lbl)
        return btn

    # ---- menus ----
    # Drawn INSIDE the panel window (a Gtk.Fixed overlay), not as their own
    # top-level. A separate menu top-level hits matchbox's flaky
    # secondary-window paint (Gtk.Menu can't even grab under matchbox;
    # override-redirect popups never paint; managed menu-toplevels paint only
    # intermittently). The panel is the primary surface matchbox always
    # paints, so a menu placed in it paints too; the X shape mask exposes the
    # menu rectangle over the desktop.
    # Panel dropdowns are drawn INSIDE the panel window (a Gtk.Fixed overlay),
    # and the panel is given INPUT FOCUS while a menu is open.
    #
    # A menu drawn in the panel can only stack as high as the panel itself, and
    # matchbox keeps the FOCUSED window on top — so with the Finder focused, the
    # panel (and its menu) sat behind it. Raising the panel was not enough
    # (matchbox re-lowered it); taking focus is the lever matchbox actually
    # obeys. Every separate-popup route failed here: a Gtk.Menu's grab dies
    # under matchbox, an override-redirect POPUP never maps, and a managed
    # popup lost the stack to the focused Finder. Drawing in the already-painted
    # panel and focusing it is what works.
    def _toggle_or_replace(self, button):
        """The shared front half of every dropdown in this bar.

        Returns True when the click merely CLOSED the menu that was already
        open, in which case the caller builds nothing at all — which is why
        this is a separate step rather than something _present could do: the
        notification card costs a directory read to build, and a second click
        on the bell must not pay for it.
        """
        self._cancel_nudges()
        if self._menu is not None:
            same = self._menu_for is button
            if same:                       # click the same button = toggle shut
                self._menu_close()         # retracts to its title (G1)
                return True
            # Switching titles is a REPLACEMENT, not a journey: the old menu
            # goes instantly and the new one drops from its own title.
            self._menu_remove()
        return False

    def _popup(self, items, button):
        """A dropdown of (label, callback[, markup[, trailing]]) rows."""
        if self._toggle_or_replace(button):
            return
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inner.get_style_context().add_class("sysmenu")
        for item in items:
            label, cb = item[0], item[1]
            markup = item[2] if len(item) > 2 else None
            # item[3], when present, is a TRAILING accessory (the active-state
            # check). It is a second, right-aligned label rather than markup
            # glued onto the end of the first, so the checks in a menu line up
            # in a column instead of landing wherever each label happens to end.
            trailing = item[3] if len(item) > 3 else None
            # item[4] says the row's text is the USER's, not ours.
            verbatim = item[4] if len(item) > 4 else False
            if label is None:
                sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
                sep.get_style_context().add_class("sysmenu-sep")
                inner.pack_start(sep, False, False, 0)
                continue
            it = Gtk.Button()
            it.set_relief(Gtk.ReliefStyle.NONE)
            it.get_style_context().add_class("sysmenu-item")
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            text = Gtk.Label(xalign=0.0)
            if verbatim:
                # A Finder label the user renamed. _t_markup looks INSIDE the
                # coloured-dot markup and translates each text RUN, so the
                # stamp has to carry the markup string itself.
                set_verbatim(text, markup or label)
                if markup:
                    text.set_markup(markup)
            else:
                text.set_label(label)
                if markup:
                    text.set_markup(markup)
            row.pack_start(text, True, True, 0)
            if trailing:
                acc = Gtk.Label(xalign=1.0)
                acc.set_markup(trailing)
                acc.get_style_context().add_class("sysmenu-check")
                row.pack_end(acc, False, False, 0)
            it.add(row)
            if cb:
                it.connect("clicked",
                           lambda _w, fn=cb: (self._menu_close(), fn()))
            else:
                it.set_sensitive(False)
            inner.pack_start(it, False, False, 0)

        self._present(inner, button)

    def _present(self, inner, button, right_edge=None,
                 idle_s=MENU_IDLE_TIMEOUT_S):
        """Put a built dropdown on screen under `button`.

        The whole back half of _popup, split out so a dropdown that is not a
        list of menu rows — the notification centre — gets the identical
        treatment: the same screen-height scroll cap, the same EventBox host,
        the same shape mask, paint nudge and idle timeout. A second copy of
        this is how two dropdowns in one bar start behaving differently.

        `right_edge`, when given, is an x the card's RIGHT side rests on
        instead of its left side starting at the button. The bar's right
        cluster is aligned to a margin, not to the bare screen edge, and a card
        dropping out of that cluster comes to rest against the line the clock,
        date and battery already end on (Paper Physics §E2).

        A card that sets `nb_scroll_only` names the one child that may scroll;
        everything above it — the notification centre's heading and its Clear
        All — stays put while the list moves under it.
        """
        self._cancel_nudges()
        inner.show_all()
        _imin, inat = inner.get_preferred_size()
        # A full card rests SHORT of the bottom of the screen, on the same
        # margin the right cluster ends on, rather than running flush into the
        # edge: the card is a sheet of paper on the desktop, and paper that
        # touches the edge of the table reads as clipped, not as full.
        avail_h = max(160, self.screen_h - PANEL_H - RIGHT_MARGIN)
        scroll_only = getattr(inner, "nb_scroll_only", None)
        if inat.height > avail_h and scroll_only is not None:
            # Scroll the LIST, not the card. Wrapping the whole card put its
            # heading inside the scroller, so forty messages scrolled the word
            # Notifications and the Clear All button off the top — and clearing
            # the tray meant scrolling all the way back up to find the control.
            _smin, snat = scroll_only.get_preferred_size()
            room = max(80, avail_h - (inat.height - snat.height))
            sw = Gtk.ScrolledWindow()
            sw.get_style_context().add_class("sysmenu-scroll")
            sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            sw.set_min_content_width(snat.width)
            sw.set_min_content_height(room)
            sw.set_max_content_height(room)
            # (see the note below: a scroller consumes its own wheel events)
            sw.add_events(Gdk.EventMask.SCROLL_MASK)
            sw.connect("scroll-event", self._menu_activity)
            inner.remove(scroll_only)
            sw.add(scroll_only)
            inner.pack_start(sw, True, True, 0)
            inner.show_all()
            body = inner
        elif inat.height > avail_h:
            body = Gtk.ScrolledWindow()
            body.get_style_context().add_class("sysmenu-scroll")
            body.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            body.set_min_content_width(inat.width)
            body.set_min_content_height(avail_h)
            body.set_max_content_height(avail_h)
            body.add(inner)
            # GtkScrolledWindow consumes wheel/trackpad events in its default
            # handler, so an ancestor EventBox is not guaranteed to see them.
            # Stamp activity on the consumer itself before scrolling proceeds.
            body.add_events(Gdk.EventMask.SCROLL_MASK)
            body.connect("scroll-event", self._menu_activity)
        else:
            body = inner

        menu = Gtk.EventBox()
        menu.get_style_context().add_class("sysmenu-host")
        menu.add(body)
        menu.add_events(
            Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.SCROLL_MASK
            | Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.KEY_PRESS_MASK
            | Gdk.EventMask.KEY_RELEASE_MASK
            | Gdk.EventMask.TOUCH_MASK
        )
        menu.connect("motion-notify-event", self._menu_activity)
        menu.connect("scroll-event", self._menu_activity)
        menu.connect("button-press-event", self._menu_activity)
        menu.connect("button-release-event", self._menu_activity)
        menu.connect("key-press-event", self._menu_key)
        menu.connect("key-release-event", self._menu_activity)
        menu.connect("touch-event", self._menu_activity)

        bx, _by = button.translate_coordinates(self.fixed, 0, 0)
        menu.show_all()
        self.fixed.put(menu, bx, PANEL_H)
        _min, nat = menu.get_preferred_size()
        if right_edge is not None:
            bx = right_edge - nat.width
        bx = max(0, min(bx, self.screen_w - nat.width))
        self.fixed.move(menu, bx, PANEL_H)
        self._menu, self._menu_for = menu, button
        button.get_style_context().add_class("open")
        self._menu_rect = (bx, PANEL_H, nat.width, nat.height)
        self._apply_shape()

        # STACK THE PANEL ON TOP. matchbox keeps the FOCUSED/ACTIVE window
        # matchbox now stacks the panel (DOCK) above dialogs at the WM level
        # (package/matchbox/0003-panel-menu-bar-above-dialogs.patch), so the
        # dropdown — drawn inside the panel — is already on top of the Finder.
        # We must NOT present()/raise_()/focus() the panel here: that fires an
        # _NET_ACTIVE_WINDOW request which DEACTIVATES the Finder underneath
        # (and did nothing useful for stacking). keep_above stays as a cheap,
        # side-effect-free backstop.
        try:
            self.set_keep_above(True)
        except Exception:
            pass

        if not self._paint_reliable:
            self._nudge_pending = True
            menu.connect("draw", self._menu_drawn)
            self._nudge_sources.append(GLib.timeout_add(300, self._nudge_once))
        menu.connect("size-allocate", self._menu_allocated)
        self._menu_active_at = GLib.get_monotonic_time()
        self._menu_idle_s = idle_s
        self._menu_timeout = GLib.timeout_add_seconds(idle_s, self._menu_idle)

        # The menu appears AT REST — no arrival animation. The drop-from-the-
        # title settle that lived here was retired 2026-08-10 on the design
        # owner's direction: the menu bar is motion-exempt.

    def _menu_allocated(self, _w, alloc):
        rect = (alloc.x, alloc.y, alloc.width, alloc.height)
        if self._menu_rect != rect:
            self._menu_rect = rect
            self._apply_shape()

    def _menu_activity(self, *_):
        # User activity only STAMPS the last-interaction time; the idle source
        # is left alone. This used to tear the GLib timeout down and build a new
        # one on every motion-notify event, i.e. dozens of main-context source
        # add/remove pairs per second of pointer travel across a menu — main
        # loop work this software-rendered stack pays for out of the same budget
        # as the panel's repaints, for a 15-second safety net that never needed
        # that resolution. _menu_idle re-arms itself instead.
        self._menu_active_at = GLib.get_monotonic_time()
        return False

    def _menu_key(self, _widget, event):
        self._menu_activity()
        if event.keyval == Gdk.KEY_Escape:
            self._menu_close()
            return True
        return False

    def _menu_idle(self):
        # The single idle-timeout source for an open menu. Close only once the
        # menu has really gone its own idle span without interaction; if
        # _menu_activity stamped it more recently, re-arm for the time that is
        # actually left, so the close still lands within a second of the
        # deadline (what the old per-event restart bought, at one timer instead
        # of hundreds).
        left = getattr(self, "_menu_idle_s", MENU_IDLE_TIMEOUT_S) - (
            GLib.get_monotonic_time() - self._menu_active_at) / 1000000.0
        if left > 0.5:
            self._menu_timeout = GLib.timeout_add_seconds(
                max(1, int(round(left))), self._menu_idle)
            return False
        self._menu_timeout = None
        self._menu_close()
        return False

    def _maybe_dismiss(self, _w, ev):
        # ANY click outside the open menu closes it — including one on the bar
        # itself, which used to be exempted and so left a menu hanging until
        # the idle timeout when somebody clicked the clock or the bare bar to
        # put it away. A click on a menu TITLE never arrives here: that button
        # has its own input window and handles the press itself, so the guard
        # only ever suppressed clicks on dead bar background.
        if self._menu_rect is None:
            return False
        mx, my, mw, mh = self._menu_rect
        if not (mx <= ev.x <= mx + mw and my <= ev.y <= my + mh):
            self._menu_close()
        return False

    def _menu_close(self, *_):
        # Every dismiss path — click-away, Escape, the idle timeout, an item
        # activating — comes through here, and the menu leaves AT ONCE: the
        # retract-to-the-title departure was retired 2026-08-10 with the
        # arrival (the menu bar is motion-exempt).
        if self._menu_timeout is not None:
            GLib.source_remove(self._menu_timeout)
            self._menu_timeout = None
        self._cancel_nudges()
        if self._menu is None:
            return False
        return self._menu_remove()

    def _menu_remove(self, *_):
        if self._menu_timeout is not None:
            GLib.source_remove(self._menu_timeout)
            self._menu_timeout = None
        if self._menu is not None:
            self.fixed.remove(self._menu)
            if self._menu_for is not None:
                self._menu_for.get_style_context().remove_class("open")
            self._menu = self._menu_for = None
            self._menu_rect = None
            self._apply_shape()
        return False

    # ---- the notification centre ----
    #
    # WHY IT EXISTS. One app, one process, fullscreen: while somebody is reading
    # in the Ebook, the USB Writer finishing its stick has nowhere to say so —
    # its own status line is behind another window and its process may already
    # be gone. The menu bar is the only surface always on screen, so anything
    # the machine finishes while the user is elsewhere is left here.
    #
    # THERE IS DELIBERATELY NO BANNER. Nothing in this OS appears over what
    # somebody is doing in order to announce itself. The spot on the bell IS the
    # arrival, and it waits: calm, and never a reason to lose a sentence you
    # were in the middle of. That is also why nothing here makes a sound.
    #
    # The card is a dropdown like every other title in this bar — drawn inside
    # the panel window for the reason _popup explains, and resting its right
    # edge on the line the rest of the cluster ends on.
    def _notify_menu(self, button):
        if self._toggle_or_replace(button):
            return
        items, marked = nbnotify.open_tray(with_status=True)
        # Opening the tray IS reading it. The mark goes to now, so everything
        # on screen stops counting as new; anything that lands WHILE it is open
        # is later than the mark and is still new when it closes. That is the
        # honest alternative to rebuilding the list under the pointer, which
        # would reset the scroll of the thing being read (Article III §2).
        self._present(self._notify_card(items), button,
                      right_edge=self.screen_w - RIGHT_MARGIN,
                      idle_s=NOTIFY_IDLE_TIMEOUT_S)
        self._notify_state = nbnotify.state_key()
        self._paint_bell(0 if marked else nbnotify.unread_count(items))

    def _notify_rebuild(self):
        """Redraw the open tray in place, after the user changed what is in it.

        Only ever reached from a click inside the card (dismiss, Clear All), so
        it is a response to an action rather than a background refresh — the
        rule against rebuilding under the pointer is about periodic reloads
        moving things nobody touched.
        """
        button = self._menu_for
        if button is None:
            return
        self._menu_remove()
        self._present(self._notify_card(nbnotify.load()), button,
                      right_edge=self.screen_w - RIGHT_MARGIN,
                      idle_s=NOTIFY_IDLE_TIMEOUT_S)
        self._notify_state = nbnotify.state_key()

    def _notify_card(self, items):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.get_style_context().add_class("sysmenu")
        card.get_style_context().add_class("nbn")

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        head.get_style_context().add_class("nbn-head")
        title = Gtk.Label(label=_t("Notifications"), xalign=0)
        title.get_style_context().add_class("nbn-head-title")
        head.pack_start(title, True, True, 0)
        if items:
            # Offered only when there is something to clear: an always-present
            # Clear All would be a permanently dead control on an empty tray,
            # and this bar has none of those.
            clear = Gtk.Button(label=_t("Clear All"))
            clear.set_relief(Gtk.ReliefStyle.NONE)
            clear.get_style_context().add_class("nbn-clear")
            clear.connect("clicked", self._notify_clear)
            head.pack_end(clear, False, False, 0)
        card.pack_start(head, False, False, 0)
        card.pack_start(self._notify_rule(), False, False, 0)
        if self._notify_error:
            error = Gtk.Label(label=self._notify_error, xalign=0)
            error.get_style_context().add_class("nbn-empty")
            error.get_style_context().add_class("warn")
            error.set_line_wrap(True)
            error.set_max_width_chars(40)
            card.pack_start(error, False, False, 0)

        if not items:
            # An empty state says what the surface is and what fills it, in
            # sentence case (Article IV §4). "No notifications" alone would say
            # only the first half.
            empty = Gtk.Label(
                label=_t("No notifications. Apps leave a message here when a "
                         "job finishes."), xalign=0)
            empty.get_style_context().add_class("nbn-empty")
            empty.set_line_wrap(True)
            empty.set_max_width_chars(40)
            card.pack_start(empty, False, False, 0)
            return card

        # The messages live in their own box so that a tray taller than the
        # screen scrolls the LIST and leaves the heading — and Clear All —
        # where they are (see _present's nb_scroll_only).
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for n, rec in enumerate(items):
            if n:
                # A hairline between messages, not a gap: the seam is what says
                # where one message ends, and revealed structure is the design
                # (Paper Physics §E1).
                rows.pack_start(self._notify_rule(inset=True), False, False, 0)
            rows.pack_start(self._notify_row(rec), False, False, 0)
        card.pack_start(rows, False, False, 0)
        card.nb_scroll_only = rows
        return card

    @staticmethod
    def _notify_rule(inset=False):
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.get_style_context().add_class(
            "nbn-seam" if inset else "nbn-rule")
        return sep

    def _notify_row(self, rec):
        """One message: who it is from and when, then what happened.

        The message body is a button — clicking it opens the app that
        posted it and takes the message away, which is the action a person
        actually wants and the only one that needs no explanation. The dismiss
        cross is a separate sibling action for messages already read here.
        """
        row = Gtk.Button()
        row.set_relief(Gtk.ReliefStyle.NONE)
        row.get_style_context().add_class("nbn-row")
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        icon = nbicons.image(
            rec.get("icon") or nbicons.glyph_for(rec.get("app") or ""),
            16, "#6E695E")
        icon.set_valign(Gtk.Align.START)
        icon.set_margin_top(3)
        line.pack_start(icon, False, False, 0)

        # Top-aligned, both of them: every row's first line then sits the same
        # distance below its seam whether the message has one line or three,
        # which is the rhythm you actually read down a list. Centring a short
        # row instead would put its baselines somewhere between two others'.
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        col.set_valign(Gtk.Align.START)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        # A notification is ANOTHER APP'S words, and most of them carry
        # something the user named — the stick that finished writing, the film
        # that finished exporting, the file that was kept. This panel is the
        # one surface in the OS that shows other apps' user text, so nothing
        # on a row is ours to translate.
        who = Gtk.Label(xalign=0)
        set_verbatim(who, rec.get("app_name") or "")
        who.get_style_context().add_class("nbn-who")
        who.set_ellipsize(Pango.EllipsizeMode.END)
        top.pack_start(who, True, True, 0)
        when = Gtk.Label(label=self._notify_when(rec.get("at", 0)), xalign=1)
        when.get_style_context().add_class("nbn-when")
        top.pack_end(when, False, False, 0)
        col.pack_start(top, False, False, 0)

        # The title is a HEADLINE, bounded at TWO lines like the body and
        # then ellipsized. One line was the rule until the panel's own
        # "This app can't be opened on this computer." was measured in the
        # languages it ships in: 34 char-widths cut the German at "auf diesem
        # Comp…" and the Japanese at "このアプリはこのコンピューターでは開け…",
        # both times losing the verb that carries the meaning, with nothing
        # anywhere else to read the rest from. A cap counted in CHARACTERS is
        # what makes CJK worse than Latin here (a CJK glyph is about two char
        # widths wide), so the second line is not a luxury in eleven of the
        # seventeen languages — it is the sentence.
        #
        # Bounding them is what makes this a list somebody can scan rather than
        # a column of paragraphs of different lengths — and it is also the only
        # language-safe way to keep the rows on a rhythm. The alternative,
        # pinning each text line to an exact multiple of the 4px unit, would
        # clip Devanagari and CJK, whose line boxes are taller than Latin's:
        # a grid bought with cut-off Chinese is not a grid, it is a bug in
        # eleven languages. So the ROW carries the rhythm (min-height, on the
        # open ladder) and the text is free to be as tall as its script needs.
        title = Gtk.Label(xalign=0)
        set_verbatim(title, rec.get("title") or "")
        title.get_style_context().add_class("nbn-msg")
        title.set_line_wrap(True)
        # WORD_CHAR, not WORD: Japanese and Chinese write no spaces, so a
        # word-only wrap would leave the whole sentence on line one and
        # ellipsize it exactly as before.
        title.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        title.set_lines(2)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_max_width_chars(34)
        col.pack_start(title, False, False, 0)

        if rec.get("body"):
            body = Gtk.Label(xalign=0)
            set_verbatim(body, rec["body"])
            body.get_style_context().add_class("nbn-body")
            body.set_line_wrap(True)
            body.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            body.set_lines(2)
            body.set_ellipsize(Pango.EllipsizeMode.END)
            body.set_max_width_chars(34)
            col.pack_start(body, False, False, 0)
        line.pack_start(col, True, True, 0)

        x = Gtk.Button()
        x.set_relief(Gtk.ReliefStyle.NONE)
        x.get_style_context().add_class("nbn-x")
        x.set_valign(Gtk.Align.START)
        x.add(nbicons.image("wclose", 12, "#6E695E"))
        x.set_tooltip_text(_t("Dismiss"))
        x.connect("clicked", self._notify_dismiss, rec)
        row.add(line)
        # The row's promise, said out loud rather than left to be guessed —
        # and the accessible name too, since every tooltip in this OS becomes
        # one (nbapp._name_hook).
        opens = self._notify_opens(rec)
        row.set_tooltip_text(_t("Open %s") % opens if opens
                             else _t("Dismiss"))
        row.connect("clicked", self._notify_open, rec)
        wrap = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        row.set_hexpand(True)
        wrap.pack_start(row, True, True, 0)
        wrap.pack_end(x, False, False, 0)
        return wrap

    def _notify_opens(self, rec):
        """The app name a click on this row would open, or "" when there is
        nothing to open — a notification from the desktop itself, or from an
        app that is no longer installed. The row still dismisses, and its
        tooltip says so instead of promising a window that never appears."""
        mod = rec.get("app") or ""
        if mod and os.path.exists(os.path.join(DE_DIR, mod + ".py")):
            return rec.get("app_name") or mod
        return ""

    def _notify_open(self, _btn, rec):
        if not nbnotify.dismiss(rec.get("id")):
            self._notify_error = _t("Could not delete “%s”") % \
                (rec.get("title") or _t("Notifications"))
            self._notify_rebuild()
            return
        self._notify_error = ""
        self._menu_close()
        if self._notify_opens(rec):
            launch(rec.get("app"), rec.get("app_name") or "")
        self._notify_state = None       # the tick re-reads and clears the mark

    def _notify_dismiss(self, _btn, rec):
        if nbnotify.dismiss(rec.get("id")):
            self._notify_error = ""
        else:
            self._notify_error = _t("Could not delete “%s”") % \
                (rec.get("title") or _t("Notifications"))
        self._notify_rebuild()

    def _notify_clear(self, _btn):
        # Clearing is one ACTION over many messages, so it reports on the
        # action: the per-message "Could not delete “%s”" borrowed here read
        # 'Could not delete “Notifications”', which quotes the card's own name
        # as though a message by that title had failed. This is the house
        # sentence for a batch delete that did not all go through (Finder says
        # the same thing after a multi-file delete), and it says how many.
        before = len(nbnotify.load())
        gone = nbnotify.clear_all()
        left = before - gone
        self._notify_error = ("" if left <= 0 else
                              _t("%d item%s could not be deleted.") %
                              (left, "" if left == 1 else "s"))
        self._notify_rebuild()          # the tray stays open, on its empty state

    def _notify_when(self, at):
        """When a message arrived, at the resolution that is still useful.

        Minutes while that is what somebody is watching, then the clock time —
        in the format the View menu chose, because two clocks in one bar
        disagreeing about 12 or 24 hours would be this OS contradicting itself.
        Then Yesterday, then a date.
        """
        try:
            at = float(at)
        except (TypeError, ValueError):
            return ""
        now = time.time()
        gap = now - at
        if gap < 60:
            # Includes a message from the near future: a clock correction can
            # leave one stamped ahead, and "in 3 hours" is a worse answer than
            # "just now" for something that has already happened.
            return _t("Just now")
        if gap < 3600:
            return _t("%d min ago") % int(gap // 60)
        local = time.localtime(at)
        days = self._days_between(local, time.localtime(now))
        if days == 0:
            fmt = "%H:%M" if self._clock_24h else "%-I:%M %p"
            try:
                return time.strftime(fmt, local)
            except ValueError:
                return time.strftime("%H:%M", local)
        if days == 1:
            return _t("Yesterday")
        try:
            return time.strftime("%-d %b", local)
        except ValueError:
            # %-d (no-pad) is a glibc extension; musl/uClibc raise instead.
            return time.strftime("%d %b", local)

    @staticmethod
    def _days_between(then, now):
        """Whole calendar days from `then` to `now`, both struct_time.

        Through nbapp.day_ordinal — the house days-from-civil arithmetic —
        rather than dividing a difference in seconds by 86400, which skips or
        repeats a day across a daylight-saving change, twice a year. And never
        time.strptime, which imports the stdlib calendar module that
        de/calendar.py shadows.
        """
        a = nbapp.day_ordinal(time.strftime("%Y-%m-%d", then))
        b = nbapp.day_ordinal(time.strftime("%Y-%m-%d", now))
        return (b - a) if (a is not None and b is not None) else 0

    def _paint_bell(self, n):
        """Show `n` unread on the menu-bar mark.

        Two things change and they change on different schedules: the SPOT is
        on or off, so it is only redrawn when that flips (a repaint here is
        CPU-drawn pixels), while the tooltip carries the exact number and
        follows every change.
        """
        unread = n > 0
        if unread != self._bell_unread:
            self._bell_unread = unread
            surf = bell_surface(unread)
            if surf is not None:
                self.bellimg.set_from_surface(surf)
            else:                       # no cairo surface: the glyph alone
                nbicons.set_image(self.bellimg, "belldot" if unread else "bell",
                                  BELL_PX, "#1A1916" if unread else "#6E695E")
        if n != self._bell_count:
            self._bell_count = n
            # The count lives here rather than on the bar, so the mark never
            # changes width and the cluster beside it never moves — and a
            # screen reader gets the exact number, which a spot cannot give.
            text = (_t("Notifications") if not n else
                    _t("%d new notification%s") %
                    (n, "" if n == 1 else "s"))
            self.bell.set_tooltip_text(text)
            self.bell.get_accessible().set_name(text)

    def _logo_menu(self, button):
        self._popup([
            ("About This Notebook…", self._about),
            (None, None),
            ("System Settings…", lambda: launch("settings", _t("Settings"))),
            ("System Monitor", lambda: launch("sysmon", _t("System Monitor"))),
            (None, None),
            ("Sleep", lambda: self._power("sleep")),
            ("Restart…", lambda: self._power("reboot")),
            ("Shut Down…", lambda: self._power("poweroff")),
        ], button)

    # The Finder button opens FOLDERS, not apps. It used to list every
    # application, duplicating the Applications folder the Finder itself shows
    # (and the .app grid) three ways over. It now mirrors the Finder's own
    # sidebar: Applications under Devices, then the Places folders — each item
    # opening a Finder window on that folder. finder.py takes the start folder
    # as argv[1] (relative to NB_HOME, "" meaning Home).
    FINDER_PLACES = [
        ("Applications", "Applications"),
        (None, None),
        ("Home", ""),
        ("Desktop", "Desktop"),
        ("Documents", "Documents"),
        ("Music", "Music"),
        ("Pictures", "Pictures"),
        ("Videos", "Videos"),
        (None, None),
        ("Trash", ".Trash"),
    ]

    def _app_switcher(self, button):
        items = []
        for label, rel in self.FINDER_PLACES:
            if label is None:
                items.append((None, None))
                continue
            items.append((label, lambda r=rel: launch_finder_at(r)))
        self._popup(items, button)

    # ---- desktop (Finder) menu-bar dropdowns ----
    # These panel menus stand in for the Finder's menu bar while the desktop is
    # active. Every item here opens or does something real — there are no dead,
    # greyed, or "coming soon" entries anywhere in the bar.
    def _file_menu(self, button):
        self._popup([
            ("New Finder Window", lambda: launch_finder()),
            (None, None),
            ("About This Notebook…", self._about),
        ], button)

    def _edit_menu(self, button):
        # No editable field lives in the panel, so the classic Cut/Copy/Paste
        # have no target and would be permanently dead. Offer real CLIPBOARD
        # actions instead — every app's text entries paste from this same X
        # selection, so copying the timestamp here or clearing the clipboard
        # are genuinely useful, working actions (no dead items).
        # the copied time matches the clock format the user chose in View, so a
        # 12-hour bar clock copies "3:45 PM", not a surprise 24-hour "15:45".
        # ...and in the interface LANGUAGE. time.strftime has no locale on
        # this image and always writes English month and weekday names, so a
        # raw stamp put "Mon 17 Aug 2026" on a Japanese install's clipboard
        # while the bar beside it read 8月17日 月. _t() is the same path the
        # date label goes through (nbi18n translates the date words inside an
        # otherwise-numeric string), so one date has one set of words.
        stamp = "%a %-d %b %Y, %H:%M" if self._clock_24h \
            else "%a %-d %b %Y, %-I:%M %p"
        self._popup([
            ("Copy Date & Time",
             lambda: self._copy_text(_t(time.strftime(stamp)))),
            (None, None),
            ("Show Clipboard…", self._show_clipboard),
            ("Clear Clipboard", self._clear_clipboard),
        ], button)

    def _view_menu(self, button):
        # A separate Finder window shares no live state with this panel and
        # reads no view file, so its sort/list controls can't be driven from
        # here. View therefore governs what the menu bar itself shows — every
        # item real, persisted, and checkable; none greyed. Active options
        # carry a signage-red check when the menu is reopened.
        self._popup([
            ("24-Hour Clock", lambda: self._toggle_view("24h"), None,
             self._check_markup(self._clock_24h)),
            ("Show Seconds", lambda: self._toggle_view("seconds"), None,
             self._check_markup(self._clock_seconds)),
            ("Show Date", lambda: self._toggle_view("date"), None,
             self._check_markup(self._show_date)),
            (None, None),
            ("About This Notebook…", self._about),
        ], button)

    def _check_markup(self, active):
        # The trailing signage-red check shown when an option is on, as its own
        # right-aligned accessory (see _popup) so every check in a menu sits in
        # the same column. The ✓ is pinned to DejaVu Sans (face=…) — the shipped
        # Nimbus Sans has no U+2713, so naming the font that carries it renders a
        # real check instead of the "tofu" box per-glyph fallback showed on real
        # hardware.
        if not active:
            return None
        return '<span foreground="#C8341E" face="DejaVu Sans">✓</span>'

    def _toggle_view(self, which):
        # flip one menu-bar View option and persist it to shell.json (same
        # $NB_HOME/.config/notebook pattern as the Finder Label); the tick
        # honours the new state on the spot.
        if which == "24h":
            new = not self._clock_24h
            if not self._persist("clock_24h", new):
                return
            self._clock_24h = new
        elif which == "seconds":
            new = not self._clock_seconds
            if not self._persist("clock_seconds", new):
                return
            self._clock_seconds = new
        elif which == "date":
            new = not self._show_date
            if not self._persist("show_date", new):
                return
            self._show_date = new
            self.datelbl.set_visible(self._show_date)
        self._pin_widths()      # the clock's widest reading just changed
        self._tick()

    # ---- Edit: real system-clipboard actions ----
    def _clipboard(self):
        return Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)

    def _copy_text(self, text):
        clip = self._clipboard()
        clip.set_text(text, -1)
        clip.store()                 # hand off to any clipboard manager (no-op
        #                              if none is running — the text is still
        #                              owned by this process and pasteable)

    def _clear_clipboard(self):
        clip = self._clipboard()
        clip.set_text("", -1)
        clip.store()

    def _show_clipboard(self):
        # Clipboard owners are other X clients and may be hung. Never enter
        # GTK's unbounded synchronous selection wait on the panel callback.
        generation = getattr(self, "_clipboard_read_generation", 0) + 1
        self._clipboard_read_generation = generation
        state = {"done": False, "timer": 0}

        def finish(text, timed_out=False):
            if (state["done"] or generation !=
                    getattr(self, "_clipboard_read_generation", 0)):
                return False
            state["done"] = True
            if state["timer"]:
                GLib.source_remove(state["timer"])
                state["timer"] = 0
            verbatim = False
            if timed_out:
                body = "Clipboard text could not be read."
            elif not text:
                body = "There is no text on the clipboard to show."
            elif not text.strip():
                body = "The clipboard contains only blank space."
            else:
                body = text if len(text) <= 2000 else text[:2000] + "\n…"
                # Only the clipboard's OWN text is reported back verbatim; the
                # three sentences above are chrome and still translate.
                verbatim = True
            self._card_dialog(_t("Clipboard"), body, scroll_body=True,
                              verbatim=verbatim)
            return False

        def received(_clipboard, text, _data=None):
            finish(text)

        state["timer"] = GLib.timeout_add(1500, finish, None, True)
        self._clipboard().request_text(received, None)
        # request returns None when there is no text to fetch (the
        # clipboard may still hold an image, or nothing) — report "no text"
        # rather than the dishonest "empty". Cap the shown text so a huge copied
        # document can't inflate the dialog past the edges of the screen.

    def _card_dialog(self, heading, body, ok_label=None, danger=False,
                     scroll_body=False, verbatim=False):
        """An undecorated Papertone dialog card — the shape every app in this OS
        uses for a confirm.

        These two were the last stock Gtk.MessageDialogs in the desktop shell.
        A MessageDialog arrives wearing the window manager's title bar and
        Adwaita's grey, which on a Papertone desktop reads as a window from a
        different computer — and one of them is the Shut Down confirm, which is
        among the most-seen dialogs in the system.

        Returns True only when the confirming button was pressed. With no
        `ok_label` it is a plain acknowledgement card with one Close button.
        """
        dlg = Gtk.Dialog(transient_for=None, modal=True)
        dlg.set_decorated(False)
        dlg.set_position(Gtk.WindowPosition.CENTER)
        dlg.get_style_context().add_class("pdlg")
        area = dlg.get_content_area()
        area.set_spacing(0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.get_style_context().add_class("pdlgbox")
        hd = Gtk.Label(label=heading, xalign=0)
        hd.get_style_context().add_class("pdlgtitle")
        box.pack_start(hd, False, False, 0)

        msg = Gtk.Label(label=body, xalign=0)
        if verbatim:
            # `body` is the user's own bytes, not chrome. nbi18n's show_all
            # walk translates any label whose text matches a catalog key, so
            # the card whose whole job is "here is what is on your clipboard"
            # showed something else: a copied "Save" was displayed as
            # "Speichern", a copied date came back rewritten. Stamping the
            # label is what makes the walk leave it alone.
            set_verbatim(msg, body)
        msg.get_style_context().add_class("pdlgmsg")
        msg.set_line_wrap(True)
        msg.set_width_chars(40)
        msg.set_max_width_chars(46)
        if scroll_body:
            # The clipboard can hold a whole document; it must not be allowed to
            # inflate the card past the edges of the screen. A FIXED 260px box
            # is the other half of that mistake, though: one copied timestamp
            # then floated in the middle of an empty card the size of a page.
            # The scroller grows with what it holds and only starts scrolling
            # at the cap, and the text sits under the heading, not centred in
            # the leftover room.
            msg.set_selectable(True)
            msg.set_valign(Gtk.Align.START)
            # A 4000-character paste with no spaces in it — a URL, a base64
            # blob — has nowhere to wrap, and gave the card a 14000px width.
            msg.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            sw = Gtk.ScrolledWindow()
            sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            sw.set_propagate_natural_height(True)
            sw.set_max_content_height(260)
            sw.add(msg)
            box.pack_start(sw, True, True, 0)
        else:
            box.pack_start(msg, False, False, 0)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        cancel = Gtk.Button(label=_t("Cancel") if ok_label else _t("Close"))
        cancel.get_style_context().add_class("pdlgcancel")
        cancel.connect("clicked",
                       lambda *_: dlg.response(Gtk.ResponseType.CANCEL))
        row.pack_end(cancel, False, False, 0)
        if ok_label:
            ok = Gtk.Button(label=ok_label)
            ok.get_style_context().add_class("danger" if danger else "pdlgok")
            ok.connect("clicked", lambda *_: dlg.response(Gtk.ResponseType.OK))
            row.pack_end(ok, False, False, 0)
        box.pack_start(row, False, False, 0)
        area.add(box)
        # Cancel is the default and holds focus, so a stray Return or Escape can
        # never power the machine off.
        cancel.set_can_default(True)
        dlg.set_default(cancel)
        dlg.set_default_response(Gtk.ResponseType.CANCEL)
        # ...and it takes the focus before the card is shown, so the selectable
        # clipboard body cannot be handed the initial focus and select itself
        # (see _about).
        dlg.set_focus(cancel)
        dlg.show_all()
        cancel.grab_focus()
        resp = dlg.run()
        dlg.destroy()
        return resp == Gtk.ResponseType.OK

    def _label_menu(self, button):
        # Mac OS 7 Finder "Label" menu: "None" plus a set of named colour
        # labels. This panel is a separate process from any Finder window and
        # shares no live selection with it, so picking a label sets ONE session
        # label state (persisted to shell.json) rather than tagging a file. The
        # active label carries a signage-red check when the menu is reopened —
        # honest feedback, and no dead or mislabeled items.
        items = [(_t("None"), lambda: self._set_label(None),
                  self._label_item_markup(_t("None"), None),
                  self._check_markup(self._label_idx is None))]
        items.append((None, None))
        for i, color in enumerate(FINDER_LABEL_COLORS):
            name = self._label_names[i]
            # An unnamed label is still selectable, and still needs to look like
            # a row you can pick: it shows its colour beside a muted dash, not a
            # dot floating on a blank line.
            items.append((name or "\u2014",
                          lambda n=i: self._set_label(n),
                          self._label_item_markup(name, color),
                          self._check_markup(self._label_idx == i),
                          True))          # the name is the user's own
        items.append((None, None))
        items.append((_t("Edit Labels\u2026"), self._edit_labels, None))
        self._popup(items, button)

    def _sane_label_names(self, saved):
        """Six label names, each possibly EMPTY (an unnamed colour). Guards a
        hand-edited or truncated shell.json: a short list or a non-list simply
        yields unnamed labels rather than a menu with missing entries."""
        out = []
        for i in range(N_LABELS):
            v = saved[i] if isinstance(saved, list) and i < len(saved) else None
            v = v.strip()[:24] if isinstance(v, str) else ""
            out.append(v)
        return out

    def _edit_labels(self):
        """Rename the six labels, Mac OS 7 Labels-control-panel style: one row
        per label, its colour swatch beside an entry holding the name."""
        dlg = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        dlg.set_title(_t("Labels"))
        dlg.set_modal(True)
        dlg.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        dlg.set_resizable(False)
        dlg.set_position(Gtk.WindowPosition.CENTER)
        dlg.set_keep_above(True)
        dlg.get_style_context().add_class("nbabout")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.set_margin_top(26); box.set_margin_bottom(22)
        box.set_margin_start(34); box.set_margin_end(34)
        dlg.add(box)

        head = Gtk.Label(label=_t("Labels"), xalign=0)
        head.get_style_context().add_class("nbabout-name")
        box.pack_start(head, False, False, 0)
        sub = Gtk.Label(label=_t("Rename the labels in the Label menu."), xalign=0)
        sub.get_style_context().add_class("nbabout-key")
        sub.set_margin_bottom(16)
        box.pack_start(sub, False, False, 0)

        grid = Gtk.Grid(row_spacing=9, column_spacing=12)
        entries = []
        for i, color in enumerate(FINDER_LABEL_COLORS):
            name = self._label_names[i]
            dot = Gtk.Label()
            dot.set_markup('<span foreground="%s" face="DejaVu Sans" '
                           'size="x-large">\u25cf</span>' % color)
            ent = Gtk.Entry()
            ent.set_text(name)
            ent.set_width_chars(20)
            ent.set_max_length(24)
            entries.append(ent)
            grid.attach(dot, 0, i, 1, 1)
            grid.attach(ent, 1, i, 1, 1)
        box.pack_start(grid, False, False, 0)

        row = Gtk.Box(spacing=10)
        row.set_halign(Gtk.Align.END)
        row.set_margin_top(20)
        cancel = Gtk.Button(label=_t("Cancel"))
        cancel.get_style_context().add_class("nbabout-btn")
        cancel.connect("clicked", lambda *_: dlg.destroy())
        save = Gtk.Button(label=_t("Save"))
        save.get_style_context().add_class("nbabout-btn")

        def _save(*_a):
            # A cleared field means an UNNAMED colour, which is the default
            # state — it is not an error to be filled in with placeholder copy.
            names = [ent.get_text().strip()[:24] for ent in entries]
            # The selection is an index, so a rename cannot lose it.
            if not self._persist("label_names", names):
                return
            self._label_names = names
            dlg.destroy()

        save.connect("clicked", _save)
        row.pack_end(save, False, False, 0)
        row.pack_end(cancel, False, False, 0)
        box.pack_start(row, False, False, 0)
        # Return saves. Typing IS the task here, and every other dialog in this
        # OS gives its confirming button the default (nbpicker, firstrun, the
        # panel's own _card_dialog); in this one Return did nothing at all, so
        # a name typed and confirmed the way every other field in the system
        # takes one was simply not written.
        save.set_can_default(True)
        dlg.set_default(save)
        for ent in entries:
            ent.set_activates_default(True)
        dlg.connect("key-press-event", lambda _w, ev: (
            dlg.destroy() if ev.keyval == Gdk.KEY_Escape else None))
        dlg.show_all()

    def _label_item_markup(self, name, color):
        # a coloured dot for the label (hollow for "None"), then the name; the
        # active label's red check is a separate right-aligned accessory (see
        # _popup) so the checks line up. The dot glyphs are pinned to DejaVu Sans
        # (face=…) so they render from the font that is guaranteed to carry them
        # — the shipped Nimbus Sans has no U+25CB (○); this removes any
        # dependence on per-glyph fallback, which showed "tofu" boxes on real
        # hardware.
        if color:
            dot = '<span foreground="%s" face="DejaVu Sans">●</span>   ' % color
        else:
            dot = '<span foreground="#9A9484" face="DejaVu Sans">○</span>   '
        # A label the user has not named yet is shown as a muted dash rather
        # than nothing at all, so the row still reads as a pickable label.
        text = (GLib.markup_escape_text(name) if name
                else '<span foreground="#9A9484">—</span>')
        return dot + text

    def _set_label(self, idx):
        if self._persist("finder_label_idx", idx):
            self._label_idx = idx

    def _load_prefs(self):
        # the whole persisted session dict (Finder Label + menu-bar View
        # prefs); callers pull their own keys with .get(). Matches every app's
        # $NB_HOME/.config/notebook/<name>.json persistence pattern.
        try:
            with open(SHELL_FILE) as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _persist(self, key, value):
        # read-modify-write ONE key of shell.json, preserving every other key
        # (so saving a View toggle never drops the Finder Label, and vice versa).
        # Write to a temp file and atomically replace, so a crash or power cut
        # mid-write can never leave a truncated shell.json that resets EVERY
        # remembered pref (label + all View toggles) on the next boot.
        try:
            os.makedirs(CFG_DIR, exist_ok=True)
            data = self._load_prefs()
            data[key] = value
            nbapp.atomic_write_json(SHELL_FILE, data)
            return True
        except Exception as exc:
            try:
                # app_name, not app: "System" is who is speaking, and the
                # module field would make the row offer to open a de/System.py
                # that does not exist. Without a name the row's sender line
                # was blank.
                nbnotify.post(nbapp.save_failure_reason(exc, SHELL_FILE),
                              app_name=_t("System"))
            except Exception:
                pass
            return False

    def _about(self):
        """About This Notebook — the snail mark over plain machine facts.

        Deliberately free of marketing copy: it used to lead with "Offline by
        design.", which describes the product rather than this machine. What
        belongs here is what the user cannot otherwise look up — the release,
        the system core it is running, and the date the image was built."""
        dlg = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        dlg.set_title(_t("About This Notebook"))
        dlg.set_modal(True)
        dlg.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        dlg.set_resizable(False)
        dlg.set_position(Gtk.WindowPosition.CENTER)
        dlg.set_keep_above(True)
        dlg.get_style_context().add_class("nbabout")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.set_margin_top(34); box.set_margin_bottom(26)
        box.set_margin_start(46); box.set_margin_end(46)
        dlg.add(box)

        # the snail mark, at the size it was drawn for
        try:
            from gi.repository import GdkPixbuf
            pb = GdkPixbuf.Pixbuf.new_from_file_at_size(
                "/opt/notebook/logo.png", 96, 96)
            img = Gtk.Image.new_from_pixbuf(pb)
            img.set_margin_bottom(14)
            box.pack_start(img, False, False, 0)
        except Exception:
            pass                      # no logo on disk: the text still stands

        name = Gtk.Label(label=nbapp.nb_pretty_name())
        name.get_style_context().add_class("nbabout-name")
        box.pack_start(name, False, False, 0)

        rule = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        rule.set_margin_top(18); rule.set_margin_bottom(16)
        box.pack_start(rule, False, False, 0)

        # machine facts, label/value in two columns
        grid = Gtk.Grid(row_spacing=9, column_spacing=26)
        grid.set_halign(Gtk.Align.CENTER)
        rows = []
        kernel = read_first_line("/proc/sys/kernel/osrelease")
        if kernel:
            # Same two labels the Settings About page uses for these exact
            # values — one machine fact cannot have two names in one OS.
            rows.append((_t("System core"), kernel))
        built = nbapp.os_release_field("BUILD_ID")
        if built:
            rows.append((_t("Built"), built))
        mem = self._about_memory()
        if mem:
            rows.append((_t("Memory"), mem))
        for r, (k, v) in enumerate(rows):
            kl = Gtk.Label(label=k, xalign=1)
            kl.get_style_context().add_class("nbabout-key")
            vl = Gtk.Label(label=v, xalign=0)
            vl.get_style_context().add_class("nbabout-val")
            vl.set_selectable(True)
            grid.attach(kl, 0, r, 1, 1)
            grid.attach(vl, 1, r, 1, 1)
        box.pack_start(grid, False, False, 0)

        close = Gtk.Button(label=_t("Close"))
        close.get_style_context().add_class("nbabout-btn")
        close.set_halign(Gtk.Align.CENTER)
        close.set_margin_top(24)
        close.connect("clicked", lambda *_: dlg.destroy())
        box.pack_start(close, False, False, 0)

        dlg.connect("key-press-event", lambda _w, ev: (
            dlg.destroy() if ev.keyval in (Gdk.KEY_Escape, Gdk.KEY_Return)
            else None))
        # Focus BEFORE the window is shown. gtk_window_show hands the initial
        # focus to the first focusable widget, and here that is a selectable
        # value label — which selects all of its own text on the way in, so the
        # card opened with the kernel string sitting on a grey selection box
        # nobody had dragged over. Moving focus afterwards does not clear that
        # selection; never getting it does.
        dlg.set_focus(close)
        dlg.show_all()
        close.grab_focus()

    def _about_memory(self):
        """Total RAM as a plain 'N.N GB', or "" if /proc/meminfo is unreadable."""
        try:
            with open("/proc/meminfo") as fh:
                for ln in fh:
                    if ln.startswith("MemTotal:"):
                        kb = int(ln.split()[1])
                        return "%.1f GB" % (kb / 1048576.0)
        except Exception:
            pass
        return ""

    def _power(self, mode):
        # Sleep just blanks the screen — nothing is at risk, so it fires at
        # once. Restart / Shut Down tear down every open app together with its
        # UNSAVED WORK, so a single stray click must not trigger them: the
        # ellipsis in the menu promises this confirmation, and now it delivers.
        if mode == "sleep":
            # login owns the order: on a password-protected machine it first
            # maps the fullscreen lock surface, then blanks.  Blanking here
            # exposed the live desktop when an immediate wake beat Python/GTK
            # startup.  Passwordless systems still blank immediately in login.
            try:
                import subprocess
                subprocess.Popen(["python3", "/opt/notebook/de/login.py",
                                  "--lock", "--sleep"])
            except OSError:
                pass
        else:
            self._confirm_power(mode)

    def _confirm_power(self, mode):
        verb = "Shut Down" if mode == "poweroff" else "Restart"
        if self._card_dialog(_t("%s?") % _t(verb),
                             _t("Unsaved work in open apps will be lost."),
                             ok_label=_t(verb), danger=True):
            self._do_power(mode)

    def _do_power(self, mode):
        # busybox provides poweroff/reboot. Only ever reached after confirm.
        try:
            import subprocess
            subprocess.Popen([mode])
        except OSError:
            pass

    def _pin_widths(self):
        """Reserve, for each read-out in the right cluster, the width of its
        WIDEST possible text, so the cluster never shifts as the values change.

        The widths are measured with the labels' own Pango layouts (which carry
        the CSS font), not guessed from a character count, and are re-measured
        whenever the clock format changes. Each read-out is measured against
        EVERY string it can show and the widest wins: a single hand-picked
        sample kept missing the real maximum by a pixel ("AM" is wider than
        "PM", "May" wider than "Sep"), and a pixel is exactly the drift this
        method exists to prevent."""
        if self._clock_seconds:
            clocks = (("88:88:88",) if self._clock_24h
                      else ("88:88:88 AM", "88:88:88 PM"))
        else:
            clocks = ("88:88",) if self._clock_24h else ("88:88 AM", "88:88 PM")
        for lbl, samples in ((self.clocklbl, clocks),
                             (self.datelbl, self._date_samples()),
                             (self.batlbl, ("100%+",))):
            try:
                # Measure what will be SHOWN, which is the translated form.
                # create_pango_layout is a raw Pango call and does not go
                # through nbi18n the way set_markup does, so this measured
                # English and displayed Spanish: "Dom 28 de mayo" is 25px
                # wider than "Wed 28 May", and set_size_request is a MINIMUM,
                # so the label simply grew past its reservation and the whole
                # right cluster shifted as the date changed — the exact drift
                # this method exists to prevent. Eight of the seventeen
                # languages were over; only English was ever right by luck.
                w = max(lbl.create_pango_layout(_t(s)).get_pixel_size()[0]
                        for s in samples)
                lbl.set_size_request(w, -1)
            except Exception:
                pass          # unmeasurable: the bar just reflows as before

    @staticmethod
    def _date_samples():
        """Every reading the bar date can take, at a two-digit day. The weekday
        and month names are translated, so they are produced with strftime
        rather than listed here. %d (not the bar's %-d) because a padded day is
        the wide case; it also sidesteps the musl %-d rejection guarded for
        elsewhere. Never time.strptime — that pulls in the stdlib calendar
        module, which de/calendar.py shadows."""
        out = []
        for mon in range(1, 13):
            for wd in range(7):
                st = time.struct_time((2026, mon, 28, 12, 0, 0, wd, 1, 0))
                try:
                    out.append(time.strftime("%a %d %b", st))
                except (ValueError, TypeError):
                    pass
        return out or ("Wed 88 Sep",)

    def _sync_timezone(self):
        """Follow a time-zone change without waiting for a restart.

        With no zoneinfo in the image there is no /etc/localtime to re-point,
        so the only lever is the TZ environment variable — and that reaches
        exactly one process. Picking a zone in Settings therefore changed
        Settings and nothing else: this clock went on showing the old zone
        until the session was restarted.

        Fixing it HERE fixes more than the clock. Apps are launched from this
        process with a copy of its environment (see the Popen calls above), so
        once the panel has the new zone every app opened afterwards starts in
        it too. Apps ALREADY open keep the zone they started in; no process can
        reach into another's environment, and that limit is stated on the
        Settings page rather than papered over.

        A stat per tick, not a read: the same shape as the notification spool
        above, and settings.json is only opened on a tick where it changed.
        """
        try:
            st = os.stat(SETTINGS_FILE)
            stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            return
        if stamp == self._tz_stamp:
            return
        self._tz_stamp = stamp
        was = os.environ.get("TZ")
        try:
            nbprefs.apply_timezone()
        except Exception:                                   # noqa: BLE001
            return          # a damaged settings.json must not stop the clock
        if os.environ.get("TZ") != was:
            # Force the labels below to redraw. They are compared against their
            # last value to avoid needless repaints, and 10:30 in one zone is
            # the same STRING as 10:30 in another — so without this the clock
            # would keep the old text until the minute happened to roll over.
            self._last_clock = self._last_tip = self._last_date = None

    def _tick(self):
        self._sync_timezone()
        now = time.localtime()
        if self._clock_seconds:
            fmt = "%H:%M:%S" if self._clock_24h else "%-I:%M:%S %p"
        else:
            fmt = "%H:%M" if self._clock_24h else "%-I:%M %p"
        # Only touch a label when its text actually changed. With seconds off
        # the clock string is identical for 59 of every 60 ticks, and the bar
        # date changes just once a day; re-setting an unchanged label would
        # queue a needless repaint every second, and on this GPU-less
        # software-rendered stack every repaint is pixels the CPU must redraw.
        clock = time.strftime(fmt, now)
        if clock != self._last_clock:
            self._last_clock = clock
            self.clocklbl.set_text(clock)
        # hovering the clock surfaces the full date — weekday, day, month and
        # the YEAR the compact bar date omits — even when Show Date is off.
        tip = time.strftime("%A, %-d %B %Y", now)
        if tip != self._last_tip:
            self._last_tip = tip
            self.clocklbl.set_tooltip_text(tip)
        date = time.strftime("%a %-d %b", now)
        if date != self._last_date:
            self._last_date = date
            self.datelbl.set_markup('<span foreground="#6E695E">%s</span>'
                                    % date)
        # Battery % after the date. Read is a couple of tiny /sys files inside a
        # tick that already fires every second (no new wakeup); only relabel when
        # the reading changes, so the software-rendered bar isn't repainted for
        # 59 of every 60 identical ticks.
        # The notification spool. state_key() stats both spools and lists them —
        # never opens a record — so the 59 ticks in a minute where nothing was
        # posted cost nothing and touch no widget (Constitution B8). Only a
        # changed key pays for reading the tray.
        state = nbnotify.state_key()
        if state != self._notify_state:
            self._notify_state = state
            self._paint_bell(nbnotify.unread_count())
        bat, bat_tip = self._battery_pct()
        self._paint_battery(bat, bat_tip)
        return True

    def _paint_battery(self, bat, bat_tip):
        """Update battery ink and hover detail on their own change clocks."""
        if bat != self._last_bat:
            self._last_bat = bat
            if bat is None:
                self.batlbl.set_visible(False)
            else:
                # A nearly-flat battery read exactly like a full one — same
                # muted grey — so the machine could die with no warning at all.
                # Signage red is the design's alert colour; this is an alert.
                low = (not bat.endswith("+")
                       and bat[:-1].isdigit() and int(bat[:-1]) <= 10)
                self.batlbl.set_markup(
                    '<span foreground="%s">%s</span>'
                    % ("#C8341E" if low else "#6E695E", bat))
                self.batlbl.set_visible(True)
        if bat_tip != self._last_bat_tip:
            self._last_bat_tip = bat_tip
            self.batlbl.set_tooltip_text(bat_tip or "")

    def _battery_pct(self):
        """(text, tooltip) for the bar battery read-out, or (None, None) when
        the machine has no battery. text is e.g. '83%' ('+' suffix while
        charging)."""
        base = "/sys/class/power_supply"
        try:
            entries = sorted(os.listdir(base))
        except OSError:
            return None, None

        def rd(path):
            try:
                with open(path) as fh:
                    return fh.read().strip()
            except OSError:
                return ""

        batteries = []
        for e in entries:
            p = os.path.join(base, e)
            if rd(os.path.join(p, "type")) != "Battery":
                continue
            # Skip peripheral batteries (wireless mouse/keyboard etc.) — those
            # report scope "Device"; the system battery is "System" or unscoped.
            # Without this the bar could show the mouse's charge, not the laptop's.
            if rd(os.path.join(p, "scope")) == "Device":
                continue
            status = rd(os.path.join(p, "status"))

            def number(name):
                try:
                    value = float(rd(os.path.join(p, name)))
                    return value if value >= 0 else None
                except (TypeError, ValueError):
                    return None

            batteries.append({
                "capacity": number("capacity"),
                "energy": (number("energy_now"), number("energy_full")),
                "charge": (number("charge_now"), number("charge_full")),
                "status": status,
            })
        if not batteries:
            return None, None

        def aggregate(field):
            pairs = [battery[field] for battery in batteries]
            if not all(now is not None and full is not None and full > 0
                       for now, full in pairs):
                return None
            return 100.0 * sum(now for now, _full in pairs) / sum(
                full for _now, full in pairs)

        pct = aggregate("energy")
        if pct is None:
            pct = aggregate("charge")
        if pct is None:
            # Mixed firmware schemas are common (internal energy_* plus a
            # removable pack exposing only capacity). Derive one percentage
            # per pack so the fallback never silently drops a battery.
            capacities = []
            for battery in batteries:
                value = battery["capacity"]
                for field in ("energy", "charge"):
                    now, full = battery[field]
                    if value is None and now is not None and full is not None and full > 0:
                        value = 100.0 * now / full
                if value is None:
                    return None, None
                capacities.append(value)
            if not capacities:
                return None, None
            pct = sum(capacities) / len(capacities)
        pct = max(0, min(100, int(round(pct))))

        statuses = [battery["status"] for battery in batteries
                    if battery["status"]]
        charging = any(status == "Charging" for status in statuses)
        if charging:
            status = "Charging"
        elif statuses and all(value == "Full" for value in statuses):
            status = "Full"
        elif "Discharging" in statuses:
            status = "Discharging"
        else:
            status = statuses[0] if statuses else ""
        cap = str(pct)
        txt = cap + "%" + ("+" if charging else "")
        # Assembled from TRANSLATED parts. nbi18n can only look a WHOLE string
        # up, and no catalog entry can carry "Battery 7%  ·  Discharging" with
        # a live number inside it — so the one read-out in the right cluster
        # that was built by concatenation was also the only hover text that
        # stayed English in all seventeen languages, beside a clock and a bell
        # that translate. The sysfs status words are catalog keys already
        # ("Charging", "Discharging", "Full", "Not charging"); a word no
        # catalog knows is shown as the kernel spells it rather than dropped.
        #
        # The multi-pack count is the one part still written in English. No
        # catalog carries a plural key for it, and a machine with two packs is
        # rare enough that inventing one here — an English string in seventeen
        # languages, which i18n_source_coverage rightly fails a module for —
        # would be a worse trade than leaving these two words as they were.
        detail = (("  ·  " + _t(status)) if status else "")
        if len(batteries) > 1:
            detail += "  ·  %d batteries" % len(batteries)
        return txt, _t("Battery") + " " + cap + "%" + detail

    def _reserve_strut(self, *_):
        # reserve PANEL_H at the top so maximised windows don't cover the bar
        win = self.get_window()
        if win is None:
            return
        try:
            Gdk.property_change  # ensure module present
            atom = Gdk.Atom.intern("_NET_WM_STRUT_PARTIAL", False)
            card = Gdk.Atom.intern("CARDINAL", False)
            data = [0, 0, PANEL_H, 0, 0, 0, 0, 0, 0, self.screen_w, 0, 0]
            Gdk.property_change(win, atom, card, 32,
                                Gdk.PropMode.REPLACE, data, len(data))
        except Exception:
            pass


CSS = b"""
.menubar { background: #F4F2EC; border-bottom: 1px solid #C9C4B6; }
.menubar, .menubar * {
  font-family: "Nimbus Sans","Helvetica",sans-serif;
  color: #1A1916;
}
/* A menu title is TEXT ON THE BAR, not a control sitting on it. Without an
   explicit transparent fill each title took Papertone's default button paper
   (#F8F7F2) over the #F4F2EC bar, so the whole left cluster read as a row of
   pale chips divided by two-pixel seams. Only hover/open paint a fill. */
.menuitem { padding: 4px 8px; margin: 0; border-radius: 6px;
            background: #F4F2EC; background-image: none;
            border: 1px solid #F4F2EC; font-size: 15px; }
/* hover / open-menu state: the canonical darker-beige selection swatch
   (#EAE3D2, exactly the dropdown + Finder selection) - never black, and never
   a one-off greige, so the toolbar buttons match the rest of the system. */
.menuitem:hover, .menuitem.open { background: #EAE3D2; color: #1A1916; }
.menuitem .bold { font-weight: 700; }
.menuitem.logo { padding: 3px 8px; }
/* The notification mark. Tighter than a text title because its glyph is
   already inset inside its own 17px box, so the usual 8px would read as a
   wider gap than the 18px the rest of the cluster is spaced on. */
.menuitem.bell { padding: 4px 6px; }
.clock { font-weight: 600; font-size: 14px; }
.date  { font-size: 14px; color: #6E695E; }

/* Dropdown menu: warm paper card with a DARKER-BEIGE border (never black,
   per the design language) and a soft drop shadow for separation from the
   papertone desktop below. */
/* Host EventBox for the dropdown: an opaque papertone fill so its own native
   GdkWindow (and the card's soft shadow) never scans out black without a
   compositor. */
.sysmenu-host { background: #F8F7F2; }
/* Gtk.Menu popup: the menu node AND its decoration/window paint paper so no
   black or theme-grey shows at the popup's edges under the compositor. */
menu.sysmenu, menu.sysmenu decoration, .sysmenu {
           background: #F8F7F2; border: 1px solid #C9C4B6; border-radius: 12px;
           padding: 4px 0; box-shadow: 3px 3px 0 rgba(26,25,22,0.15); }
menu.sysmenu menuitem { padding: 0; }
.sysmenu-item { padding: 6px 24px 6px 16px; margin: 0; border-radius: 0;
                font-size: 14px; background: transparent; border: none;
                box-shadow: none; min-width: 190px; color: #1A1916; }
.sysmenu-item * { font-family: "Nimbus Sans","Helvetica",sans-serif;
                  color: #1A1916; }
/* the active-state check, right-aligned in its own column at the end of a row */
.sysmenu-check { margin-left: 24px; }
/* selected item: darker-beige chrome with dark ink (NOT a black bar) - keeps
   the Label dots and the signage-red View checks legible on hover too. */
.sysmenu-item:hover, menuitem.sysmenu-item:hover,
menuitem.sysmenu-item:selected { background: #EAE3D2; }
.sysmenu-item:hover, .sysmenu-item:hover * { color: #1A1916; }
.sysmenu-item:disabled, .sysmenu-item:disabled * { color: #9A9484; }
.sysmenu-sep { background: #D7D2C5; min-height: 1px; margin: 4px 10px; }

/* A very long menu (the app switcher on a short panel) scrolls within the
   screen instead of running off the bottom edge. Slim papertone scrollbar +
   darker-beige slider so it matches the card, never a stock theme swatch. */
.sysmenu-scroll, .sysmenu-scroll viewport { background: #F8F7F2; }
.sysmenu-scroll scrollbar { background: #F1EEE6; border: none; }
.sysmenu-scroll scrollbar slider {
  background: #C9C4B6; border: 3px solid #F1EEE6; border-radius: 6px;
  min-width: 8px; min-height: 30px;
}
.sysmenu-scroll scrollbar slider:hover { background: #9A9484; }

/* ------------------------------------------------------------------------
   The notification centre.

   Same card as the menus above -- .nbn is added ALONGSIDE .sysmenu, so the
   paper, the border, the radius and the shadow are the menu's and cannot
   drift from it. What this adds is the inside of the card: a heading row, a
   full-bleed rule under it, and message rows separated by inset seams.

   Nothing here is a new colour: ink for the message, muted for who sent it and
   when, the seam tones the rest of the OS already uses for a rule inside a
   panel (#D7D2C5) and between surfaces (#C9C4B6), and the same darker-beige
   #EAE3D2 the menus light a row with.
   ------------------------------------------------------------------------ */
/* ONE width, whether the tray is full or empty: the notification centre is a
   fixed surface in this bar, not a box that shrinks to whatever is in it. The
   value is measured, not guessed: it is what puts the card's RENDERED width
   (this interior plus the 1px border pair) on the 4px unit. */
.nbn { min-width: 358px; padding: 0; }
/* Heading row and message rows both state their interior height, so the
   card's rhythm is declared rather than left to whatever the text
   measured out at: 32 and 48 are 8u and 12u, and each renders 16px taller
   than its interior for the 8px padding pair (48 and 64, both on the open
   ladder). Floors, not fixed heights: a second line of body, or a script
   with taller line boxes, grows the row instead of being clipped. */
.nbn-head { padding: 8px 10px 8px 14px; min-height: 32px; }
/* The card's own name, in the quiet register section labels use across the OS
   -- it is a heading, not a control, and must not compete with the messages. */
.nbn-head-title { font-size: 13px; color: #6E695E; letter-spacing: 0.06em; }
.nbn-clear { padding: 2px 8px; margin: 0; min-height: 24px; font-size: 13px;
             color: #6E695E; background: transparent; background-image: none;
             border: 1px solid transparent; border-radius: 6px;
             box-shadow: none; }
.nbn-clear label { color: #6E695E; }
.nbn-clear:hover { background: #EAE3D2; }
.nbn-clear:hover label { color: #1A1916; }
/* The rule under the heading runs the full width of the card (it separates two
   PARTS of it); the seams between messages are inset to the text column, the
   way a rule between entries in a list is. */
.nbn-rule { background: #C9C4B6; min-height: 1px; margin: 0; }
.nbn-seam { background: #D7D2C5; min-height: 1px; margin: 0 12px; }
.nbn-row { padding: 8px 10px 8px 14px; margin: 0; border: none;
           border-radius: 0; background: transparent; background-image: none;
           box-shadow: none; min-height: 48px; }
.nbn-row:hover { background: #EAE3D2; }
/* Sender and time are one tier of information and share one tone. The quieter
   @muted-3 was tried for the time and measured 2.87:1 against the row's hover
   swatch, under the 3:1 floor for an 11px string somebody is meant to read;
   the hierarchy is carried by SIZE here, and does not need a second colour to
   buy it at the cost of legibility. */
.nbn-who { font-size: 11px; color: #6E695E; letter-spacing: 0.04em; }
.nbn-when { font-size: 11px; color: #6E695E; }
.nbn-msg { font-size: 14px; color: #1A1916; }
.nbn-body { font-size: 13px; color: #6E695E; }
/* The dismiss cross. 24px square is the hard floor for anything actionable
   (Article VII 4); it stays quiet until the pointer is on the row. */
.nbn-x { padding: 0; margin: 0; min-width: 24px; min-height: 24px;
         border: 1px solid transparent; border-radius: 6px;
         background: transparent; background-image: none; box-shadow: none; }
.nbn-x:hover { background: #DED4C2; }
.nbn-empty { padding: 16px 14px; font-size: 13px; color: #6E695E; }
/* The tray's own failure line, which shares the empty state's shape. It needs
   the alert register or it reads as one more quiet sentence of chrome: signage
   red, the design system's single alert colour, the same one a nearly-flat
   battery takes. Until this rule was written .warn was a class with no
   declaration anywhere in the OS, so a Clear All that could not clear looked
   exactly like body text. */
.nbn-empty.warn { color: #C8341E; }

/* The desktop shell's dialog card: same paper, rule and type as every app's
   confirm, so a system question does not look like a window from another
   computer. Undecorated, so it carries no window-manager title bar. */
.pdlg { background: #FCFBF8; border: 1px solid #C9C4B6; }
.pdlgbox { padding: 20px 22px 18px 22px; }
.pdlgtitle { font-family: "Newsreader","Liberation Serif",serif;
             font-size: 20px; color: #1A1916; }
.pdlgmsg { font-size: 13px; color: #2A2620; }
.pdlgcancel, .pdlgok { padding: 4px 16px; }
.pdlgok { background: #1A1916; color: #FCFBF8; border: 1px solid #1A1916; }
.pdlgok label { color: #FCFBF8; }

/* destructive confirm button (Restart / Shut Down): signage red, the design
   system's one accent for an alert. The label needs naming separately: the
   theme's `* { color: ink }` matches the label node directly and a direct match
   beats the colour inherited from the button, so without the second rule the
   word "Shut Down" came out near-black on the red. */
.danger, .danger:hover, .danger:focus {
  background: #C8341E; background-image: none; color: #F4F2EC;
  border: 1px solid #B12D19; padding: 4px 16px; font-weight: 600;
  text-shadow: none;
}
.danger label, .danger:hover label, .danger:focus label { color: #F4F2EC; }

/* About This Notebook: the snail mark over plain machine facts. Serif for the
   OS name (the one place the document voice is used in chrome), the same
   hairline rule and paper tones as the rest of the system. */
.nbabout { background: #FCFBF8; }
.nbabout * { font-family: "Nimbus Sans","Helvetica",sans-serif;
             color: #1A1916; }
.nbabout-name { font-family: "Nimbus Sans","Helvetica",sans-serif;
                font-size: 24px; font-weight: bold;
                letter-spacing: 0.01em; }
/* ^ The interface face, deliberately. The old serif stack led with
   Newsreader, which never shipped (usr/share/fonts/notebookos/ carries
   Nimbus Sans, Liberation Serif, Noto CJK/Devanagari) - so hardware showed
   Liberation Serif while dev hosts showed whatever they had. The class is
   shared with the Labels dialog head; one face, both places. */
.nbabout separator { background-color: #C9C4B6; min-height: 1px; }
.nbabout-key { font-size: 13px; color: #6E695E; letter-spacing: 0.06em; }
.nbabout-val { font-size: 13px; color: #2A2620; }
.nbabout-btn { padding: 7px 26px; background: #FCFBF8; color: #1A1916;
               border: 1px solid #C9C4B6; border-radius: 8px;
               box-shadow: none; font-size: 14px; }
.nbabout-btn:hover { background: #F1EEE6; }
"""


_CSS_DONE = False


def install_css():
    """Install the panel's stylesheet. Idempotent.

    Extracted out of main() so that CONSTRUCTING a Panel is enough to style it.
    It used to be inline here, which meant the panel was correct on the shipped
    desktop (which always goes through main()) and completely unstyled anywhere
    that built one directly -- an offscreen render, a UI audit, a test. The
    Finder had the identical bug and it wasted real time: its sidebar rendered
    as bordered theme buttons instead of flat rows, which reads as a design
    defect rather than as a missing stylesheet. The panel is the most-seen
    chrome in the OS, so a misleading render of it is the most expensive one to
    have. Same shape of fix as finder.install_css / nbapp.install_css."""
    global _CSS_DONE
    if _CSS_DONE:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _CSS_DONE = True
    # ...and the shared input rules, for the same reason the Finder and the
    # board now arm them: the panel does not build an nbapp.AppWindow, so it
    # never reached nbapp.install_css, where the OS-wide GDK dispatcher lives.
    # The panel declines focus (set_accept_focus(False)), so the click-to-focus
    # half is a no-op here; the focus-ring modality half is not.
    try:
        import nbapp as _nbapp
        _nbapp.track_input_modality()
    except Exception:                                             # noqa: BLE001
        pass


def main():
    install_css()

    panel = Panel()
    panel.connect("destroy", Gtk.main_quit)
    panel.show_all()

    # log a proof-of-life line to the session log
    print("Notebook OS shell up — kernel %s" %
          read_first_line("/proc/sys/kernel/osrelease"), flush=True)

    Gtk.main()


if __name__ == "__main__":
    main()
