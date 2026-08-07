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
# subprocess is imported lazily inside the functions that spawn processes
# (launch / _paint_below_bar / _power / _do_power). None of those run during
# construct or the first paint, so the boot-foreground panel never pays the
# subprocess import cost before it is on screen.

import xshape
import nbapp  # for nudge_paint (swrast scanout flush) + version/pretty name
from nbi18n import _t  # panel menu labels (Finder/File/Edit/View/Label) translate
# The shared motion engine, for the panel-menu drop (PAPER-PHYSICS G1).
# Never fatal: a panel that cannot animate must still open its menus.
try:
    import nbmotion
except Exception:                                                 # noqa: BLE001
    nbmotion = None

PANEL_H = 46
# An open dropdown auto-dismisses after this many seconds of NO interaction —
# a safety net that also reverts the whole-screen input shape if a menu is left
# hanging. Pointer movement over the menu restarts the timer (_menu_activity),
# so a menu never vanishes from under someone who is still reading it.
MENU_IDLE_TIMEOUT_S = 15
DE_DIR = os.path.dirname(os.path.abspath(__file__))

# session state (the Finder Label choice) survives close/reboot under
# $NB_HOME/.config/notebook, matching every app's persistence pattern.
HOME = os.environ.get("NB_HOME", "/root")
CFG_DIR = os.path.join(HOME, ".config", "notebook")
SHELL_FILE = os.path.join(CFG_DIR, "shell.json")

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


def launch(mod):
    # A launched app hides the desktop home (Finder + widget column) while it
    # runs. That flag (/tmp/nb-app-active) is now owned by nbapp.AppWindow and
    # ref-counted across every app process — however it was launched, including
    # the session-launched installer — so launching just starts the process.
    # finder/widgets ARE the desktop home and are not AppWindows, so they never
    # set the flag.
    import subprocess
    script = os.path.join(DE_DIR, mod + ".py")
    if os.path.exists(script):
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
        # The drop-from-the-title arrival (PAPER-PHYSICS G1): the menu is
        # PLACED at its final rectangle immediately — shape, stacking and
        # input all settle once — and only the PAINT travels: a draw-handler
        # translate slides the content down inside its own clip. Position is
        # never animated (Article F2), damage is the menu's rectangle only.
        self._menu_anim = None       # live nbmotion.Damaged, if animating
        self._menu_anim_v = 1.0      # 0 = tucked under the bar, 1 = at rest
        self._menu_rise = 0          # px of travel for this menu
        self._menu_closing = False   # a retract is in flight
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

        # --- right cluster: clock, date, battery ---
        right = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        right.set_margin_end(20)
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

        # last-shown strings, so the per-second tick only repaints a label when
        # its text actually changed (see _tick) — every repaint is CPU-drawn
        # pixels on this GPU-less software-rendered stack.
        self._last_clock = self._last_tip = self._last_date = None
        self._last_bat = None
        self._pin_widths()
        self._tick()
        GLib.timeout_add_seconds(1, self._tick)

        # dismiss an open menu when the pointer clicks outside it
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("button-press-event", self._maybe_dismiss)
        self.connect("realize", self._on_realize)
        # once the panel is actually on screen, tell the boot splash the desktop
        # is up so it fills to 100% and dismisses. See splash.py / session.sh.
        self.connect("map-event", self._signal_ready)

    def _signal_ready(self, *_):
        try:
            open("/tmp/nb-ready", "w").close()
        except Exception:
            pass
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
                pid = fh.read().strip()
            want_hidden = bool(pid) and os.path.isdir("/proc/" + pid)
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
        # bounding shape: only the bar (+ open menu) is drawn on screen
        visible = [bar] + ([self._menu_rect] if self._menu_rect else [])
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
    def _popup(self, items, button):
        self._cancel_nudges()
        if self._menu is not None:
            same = self._menu_for is button
            if same:                       # click the same button = toggle shut
                self._menu_close()         # retracts to its title (G1)
                return
            # Switching titles is a REPLACEMENT, not a journey: the old menu
            # goes instantly and the new one drops from its own title.
            self._menu_remove()
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
            if label is None:
                sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
                sep.get_style_context().add_class("sysmenu-sep")
                inner.pack_start(sep, False, False, 0)
                continue
            it = Gtk.Button()
            it.set_relief(Gtk.ReliefStyle.NONE)
            it.get_style_context().add_class("sysmenu-item")
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            text = Gtk.Label(label=label, xalign=0.0)
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

        inner.show_all()
        _imin, inat = inner.get_preferred_size()
        avail_h = max(160, self.screen_h - PANEL_H)
        if inat.height > avail_h:
            body = Gtk.ScrolledWindow()
            body.get_style_context().add_class("sysmenu-scroll")
            body.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            body.set_min_content_width(inat.width)
            body.set_min_content_height(avail_h)
            body.set_max_content_height(avail_h)
            body.add(inner)
        else:
            body = inner

        menu = Gtk.EventBox()
        menu.get_style_context().add_class("sysmenu-host")
        menu.add(body)
        menu.add_events(Gdk.EventMask.POINTER_MOTION_MASK)
        menu.connect("motion-notify-event", self._menu_activity)

        bx, _by = button.translate_coordinates(self.fixed, 0, 0)
        menu.show_all()
        self.fixed.put(menu, bx, PANEL_H)
        _min, nat = menu.get_preferred_size()
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
        self._menu_timeout = GLib.timeout_add_seconds(
            MENU_IDLE_TIMEOUT_S, self._menu_idle)

        # nbmotion-inventory: system.panel-menu-open
        self._menu_closing = False
        self._menu_anim_v = 1.0
        if nbmotion is not None:
            # A short damped settle, not a float: the content arrives from
            # the title's baseline and comes to rest on its own rule. The
            # draw handler translates; Damaged invalidates only the strip
            # the slide sweeps. Instant-equivalence: under Reduced Motion
            # animate_to lands synchronously and this whole block is one
            # extra draw of the final frame.
            self._menu_rise = min(18, nat.height)
            menu.connect("draw", self._menu_arrival_draw)
            self._menu_anim_v = 0.0

            def _arrive(v):
                self._menu_anim_v = v

            self._menu_anim = nbmotion.Damaged(
                widget=self,
                rect_for=lambda v, x=bx, w=nat.width, h=nat.height:
                    (x, PANEL_H - (1.0 - v) * self._menu_rise, w, h),
                on_frame=_arrive,
                duration=nbmotion.SURFACE_IN, easing=nbmotion.ARRIVE)
            self._menu_anim.animate_to(1.0)

    def _menu_arrival_draw(self, _menu, cr):
        # Slide the menu's PAINT down its column while it arrives (and back
        # up while it retracts). The widget's clip does the masking, so the
        # content emerges from under the bar exactly along its own edge.
        dy = -(1.0 - self._menu_anim_v) * self._menu_rise
        if dy:
            cr.translate(0, dy)
        return False

    def _menu_allocated(self, _w, alloc):
        rect = (alloc.x, alloc.y, alloc.width, alloc.height)
        if self._menu_rect != rect:
            self._menu_rect = rect
            self._apply_shape()

    def _menu_activity(self, *_):
        # Pointer motion only STAMPS the last-interaction time; the idle source
        # is left alone. This used to tear the GLib timeout down and build a new
        # one on every motion-notify event, i.e. dozens of main-context source
        # add/remove pairs per second of pointer travel across a menu — main
        # loop work this software-rendered stack pays for out of the same budget
        # as the panel's repaints, for a 15-second safety net that never needed
        # that resolution. _menu_idle re-arms itself instead.
        self._menu_active_at = GLib.get_monotonic_time()
        return False

    def _menu_idle(self):
        # The single idle-timeout source for an open menu. Close only once the
        # menu has really gone MENU_IDLE_TIMEOUT_S without interaction; if
        # _menu_activity stamped it more recently, re-arm for the time that is
        # actually left, so the close still lands within a second of the
        # deadline (what the old per-event restart bought, at one timer instead
        # of hundreds).
        left = MENU_IDLE_TIMEOUT_S - (
            GLib.get_monotonic_time() - self._menu_active_at) / 1000000.0
        if left > 0.5:
            self._menu_timeout = GLib.timeout_add_seconds(
                max(1, int(round(left))), self._menu_idle)
            return False
        self._menu_timeout = None
        self._menu_close()
        return False

    def _maybe_dismiss(self, _w, ev):
        # a click below the bar and outside the open menu closes it
        if self._menu_rect is None or ev.y <= PANEL_H:
            return False
        mx, my, mw, mh = self._menu_rect
        if not (mx <= ev.x <= mx + mw and my <= ev.y <= my + mh):
            self._menu_close()
        return False

    def _menu_close(self, *_):
        # nbmotion-inventory: system.panel-menu-close
        # Retract to the title (G1) and remove on arrival. Every dismiss
        # path — click-away, Escape, the idle timeout, an item activating —
        # comes through here and gets the same departure. A second close
        # while the retract is in flight, or a shell without the engine,
        # removes immediately; under Reduced Motion animate_to completes
        # synchronously, so the remove happens before this returns.
        if self._menu_timeout is not None:
            GLib.source_remove(self._menu_timeout)
            self._menu_timeout = None
        self._cancel_nudges()
        if self._menu is None:
            return False
        if nbmotion is None or self._menu_anim is None or self._menu_closing:
            return self._menu_remove()
        self._menu_closing = True
        self._menu_anim.animate_to(
            0.0, duration=nbmotion.SURFACE_OUT, easing=nbmotion.DEPART,
            on_done=lambda _ok: self._menu_remove())
        return False

    def _menu_remove(self, *_):
        if self._menu_timeout is not None:
            GLib.source_remove(self._menu_timeout)
            self._menu_timeout = None
        if self._menu_anim is not None:
            self._menu_anim.cancel()
            self._menu_anim = None
        self._menu_closing = False
        self._menu_anim_v = 1.0
        if self._menu is not None:
            self.fixed.remove(self._menu)
            if self._menu_for is not None:
                self._menu_for.get_style_context().remove_class("open")
            self._menu = self._menu_for = None
            self._menu_rect = None
            self._apply_shape()
        return False

    def _logo_menu(self, button):
        self._popup([
            ("About This Notebook…", self._about),
            (None, None),
            ("System Settings…", lambda: launch("settings")),
            ("System Monitor", lambda: launch("sysmon")),
            ("Terminal", lambda: launch("terminal")),
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
            ("Open Terminal", lambda: launch("terminal")),
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
        stamp = "%a %-d %b %Y, %H:%M" if self._clock_24h \
            else "%a %-d %b %Y, %-I:%M %p"
        self._popup([
            ("Copy Date & Time",
             lambda: self._copy_text(time.strftime(stamp))),
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
            self._clock_24h = not self._clock_24h
            self._persist("clock_24h", self._clock_24h)
        elif which == "seconds":
            self._clock_seconds = not self._clock_seconds
            self._persist("clock_seconds", self._clock_seconds)
        elif which == "date":
            self._show_date = not self._show_date
            self.datelbl.set_visible(self._show_date)
            self._persist("show_date", self._show_date)
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
        text = self._clipboard().wait_for_text()
        # wait_for_text() returns None when there is no text to fetch (the
        # clipboard may still hold an image, or nothing) — report "no text"
        # rather than the dishonest "empty". Cap the shown text so a huge copied
        # document can't inflate the dialog past the edges of the screen.
        if not text:
            body = "There is no text on the clipboard to show."
        elif not text.strip():
            body = "The clipboard contains only blank space."
        else:
            body = text if len(text) <= 2000 else text[:2000] + "\n…"
        self._card_dialog(_t("Clipboard"), body, scroll_body=True)

    def _card_dialog(self, heading, body, ok_label=None, danger=False,
                     scroll_body=False):
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
        msg.get_style_context().add_class("pdlgmsg")
        msg.set_line_wrap(True)
        msg.set_width_chars(40)
        msg.set_max_width_chars(46)
        if scroll_body:
            # The clipboard can hold a whole document; it must not be allowed to
            # inflate the card past the edges of the screen.
            msg.set_selectable(True)
            sw = Gtk.ScrolledWindow()
            sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            sw.set_size_request(-1, 260)
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
        items = [("None", lambda: self._set_label(None),
                  self._label_item_markup("None", None),
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
                          self._check_markup(self._label_idx == i)))
        items.append((None, None))
        items.append(("Edit Labels\u2026", self._edit_labels, None))
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
        cancel = Gtk.Button(label="Cancel")
        cancel.get_style_context().add_class("nbabout-btn")
        cancel.connect("clicked", lambda *_: dlg.destroy())
        save = Gtk.Button(label="Save")
        save.get_style_context().add_class("nbabout-btn")

        def _save(*_a):
            # A cleared field means an UNNAMED colour, which is the default
            # state — it is not an error to be filled in with placeholder copy.
            names = [ent.get_text().strip()[:24] for ent in entries]
            # The selection is an index, so a rename cannot lose it.
            self._label_names = names
            self._persist("label_names", names)
            dlg.destroy()

        save.connect("clicked", _save)
        row.pack_end(save, False, False, 0)
        row.pack_end(cancel, False, False, 0)
        box.pack_start(row, False, False, 0)
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
        self._label_idx = idx
        self._persist("finder_label_idx", idx)

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
        except Exception:
            pass

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
            rows.append(("System core", kernel))
        built = nbapp.os_release_field("BUILD_ID")
        if built:
            rows.append(("Built", built))
        mem = self._about_memory()
        if mem:
            rows.append(("Memory", mem))
        for r, (k, v) in enumerate(rows):
            kl = Gtk.Label(label=k, xalign=1)
            kl.get_style_context().add_class("nbabout-key")
            vl = Gtk.Label(label=v, xalign=0)
            vl.get_style_context().add_class("nbabout-val")
            vl.set_selectable(True)
            grid.attach(kl, 0, r, 1, 1)
            grid.attach(vl, 1, r, 1, 1)
        box.pack_start(grid, False, False, 0)

        close = Gtk.Button(label="Close")
        close.get_style_context().add_class("nbabout-btn")
        close.set_halign(Gtk.Align.CENTER)
        close.set_margin_top(24)
        close.connect("clicked", lambda *_: dlg.destroy())
        box.pack_start(close, False, False, 0)

        dlg.connect("key-press-event", lambda _w, ev: (
            dlg.destroy() if ev.keyval in (Gdk.KEY_Escape, Gdk.KEY_Return)
            else None))
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
            # blank the display now. "force off" only takes effect when DPMS is
            # enabled, so enable it first; the single shell keeps the two in
            # order. Moving the pointer or pressing a key wakes it (standard
            # DPMS), so Sleep is safe and needs no confirmation.
            try:
                import subprocess
                subprocess.Popen(["sh", "-c",
                                  "xset +dpms; xset dpms force off; python3 /opt/notebook/de/login.py --lock"])
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

    def _tick(self):
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
        bat, bat_tip = self._battery_pct()
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
                self.batlbl.set_tooltip_text(bat_tip or "")
                self.batlbl.set_visible(True)
        return True

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

        for e in entries:
            p = os.path.join(base, e)
            if rd(os.path.join(p, "type")) != "Battery":
                continue
            # Skip peripheral batteries (wireless mouse/keyboard etc.) — those
            # report scope "Device"; the system battery is "System" or unscoped.
            # Without this the bar could show the mouse's charge, not the laptop's.
            if rd(os.path.join(p, "scope")) == "Device":
                continue
            cap = rd(os.path.join(p, "capacity"))
            if not cap:
                continue
            status = rd(os.path.join(p, "status"))
            txt = cap + "%" + ("+" if status == "Charging" else "")
            tip = "Battery " + cap + "%" + (("  ·  " + status) if status else "")
            return txt, tip
        return None, None

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
           background: #F8F7F2; border: 1px solid #C9C4B6;
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
.nbabout-name { font-family: "Newsreader","Liberation Serif","Georgia",serif;
                font-size: 24px; font-weight: 500; letter-spacing: 0.01em; }
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
