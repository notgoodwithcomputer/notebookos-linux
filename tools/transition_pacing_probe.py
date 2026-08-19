#!/usr/bin/env python3
"""Measure PAPER-PHYSICS Article G pacing per inventory transition.

The probe invokes real transition primitives and reads only completed
``nbmotion.trace_drain()`` records.  It never infers a measurement from a
configured duration.  Offscreen runs step the engine at a nominal 60 Hz, so
frame gaps are reported but advisory; the enforced checks are a non-vacuous
trace and aggregate total duration inside the inventory token's band.

    python3 tools/transition_pacing_probe.py
    python3 tools/transition_pacing_probe.py --apply

``NB_DE_DIR`` may point at a scratch copy of the runtime modules.  This is the
supported mutation/red-proof hook; the checked-out runtime is never edited.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "tools" / "motion_inventory.json"
DEFAULT_DE = (ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
DE = Path(os.environ.get("NB_DE_DIR", DEFAULT_DE)).resolve()

# These must be set before nbmotion is imported and before a run starts.
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="transition-pacing-"))
os.environ["NB_ACCEL"] = "1"
os.environ["NB_MOTION_TRACE"] = "1"
sys.path.insert(0, str(DE))

import nbmotion  # noqa: E402
import nbtransitions  # noqa: E402


MODE = "OFFSCREEN deterministic engine stepping; gap budget ADVISORY"
TARGET = {"FEEDBACK": "feedback", "SELECT": "select",
          "SURFACE_IN": "surface-in", "SURFACE_OUT": "surface-out",
          "PAGE": "page"}
TOKEN_MS = {const: int(getattr(nbmotion, const)) for const in TARGET}


def step(scalar):
    """Finish the actual Scalar created by a primitive at nominal 60 Hz."""
    if scalar is None or not scalar.running:
        return
    start = scalar._track.t0
    span = scalar._track.dur
    count = max(2, int(round(span / 0.016)))
    for i in range(1, count + 1):
        now = start + span * i / count
        if i == count:
            # Land PAST the end, not exactly on it. `now` is a small span added
            # to a large monotonic clock (t0 is ~1e4 s), and that sum rounds to
            # the nearest representable double — sometimes DOWNWARD. Track.
            # done_at asks `(now - t0) >= dur`, so a run could finish a
            # femtosecond short, never complete, and drain as a ZERO-FRAME
            # vacuous failure. It is clock-dependent, so the same conforming
            # transition could pass one run and fail the next: measured at
            # t0=14759.36, span 0.2/0.09/0.12 land just over and span 0.16 —
            # every SURFACE_IN/SURFACE_OUT transition — lands just under.
            # The nudge is 1e-8 of the span against a ~1e-13 rounding error:
            # far too small to move a millisecond total, far too big to lose.
            now = start + span * 1.0000001
        scalar.advance(now)
    # Manual stepping bypasses _Driver._on_tick, which normally performs this
    # cleanup after advance() returns false.  Keep the global widget-driver
    # registry honest (and prevent a later short-lived probe widget reusing an
    # object id from inheriting the previous widget's driver).
    if not scalar.running:
        scalar._detach()


class ProbeWidget:
    """Minimal offscreen widget protocol; the real primitive owns all motion."""
    def __init__(self):
        self.opacity = 1.0
        self.children = []
        self.fraction = 0.0

    def add_tick_callback(self, callback):
        self._tick = callback
        return 1

    def remove_tick_callback(self, _ident):
        self._tick = None

    def connect(self, *_args):
        return 1

    def in_destruction(self):
        return False

    def queue_draw_area(self, *_args):
        pass

    def get_opacity(self):
        return self.opacity

    def set_opacity(self, value):
        self.opacity = value

    def get_children(self):
        return list(self.children)

    def add(self, child):
        self.children.append(child)
        child.parent = self

    def remove(self, child):
        self.children.remove(child)
        child.parent = None

    def get_parent(self):
        return getattr(self, "parent", None)

    def show_all(self):
        pass

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False

    def get_visible(self):
        return getattr(self, "visible", False)

    def get_mapped(self):
        return True

    def queue_draw(self):
        pass

    def destroy(self):
        pass

    def get_fraction(self):
        return self.fraction

    def set_fraction(self, value):
        self.fraction = value

    def set_no_show_all(self, value):
        self.no_show_all = value

    def set_text(self, value):
        self.text = value


class ProbeCanvas(ProbeWidget):
    """A cairo drawing surface: the allocation and device-scale calls a
    self-rendering view (Maps) asks its widget for before it tweens."""
    def get_allocated_width(self):
        return 640

    def get_allocated_height(self):
        return 420

    def get_scale_factor(self):
        return 1


class ProbeBox(ProbeWidget):
    """A Gtk.Box-shaped holder: enough of the packing protocol for a container
    replacement, which is all `nbtransitions.replace` and its `pack` callback
    touch."""
    def pack_start(self, child, *_args):
        self.add(child)

    def pack_end(self, child, *_args):
        self.add(child)


class ProbeDrawable(ProbeWidget):
    """A widget a caller draws over through a `draw` handler it later
    disconnects (nbpicker's arrival translates the body in its own draw)."""
    def disconnect(self, _handler):
        pass


class ProbeSurface(ProbeWidget):
    """One stage surface of a viewer: the visibility protocol a surface swap
    exchanges while the stage itself is invisible."""
    def __init__(self, visible=False):
        ProbeWidget.__init__(self)
        self.visible = bool(visible)

    def set_visible(self, value):
        self.visible = bool(value)


class ProbeToplevel(ProbeWidget):
    """A toplevel that can be MOVED. The splash lifts its own window rather
    than fading a fullscreen surface, so position is the animated property."""
    def __init__(self):
        ProbeWidget.__init__(self)
        self.moves = []

    def get_position(self):
        return (0, 0)

    def move(self, x, y):
        self.moves.append((int(x), int(y)))


def drive_replace(token_ms):
    holder = ProbeWidget()
    holder.add(ProbeWidget())
    nbtransitions.replace(holder, ProbeWidget(), duration=token_ms)
    first = getattr(holder, "_nbmotion_opacity", None)
    step(first)
    # The midpoint callback retargets the same Scalar for the arrival half.
    step(first)


def drive_progress(token_ms):
    bar = ProbeWidget()
    nbtransitions.smooth_fraction(bar, 0.75, duration=token_ms)
    step(getattr(bar, "_nbt_frac", None))


def drive_overlay(_token_ms):
    # present_card's traced motion is its GrowCard component.  GTK surface
    # construction cannot run without a display connection, but the actual
    # GrowCard primitive and Scalar are fully offscreen-driveable.
    card = nbtransitions.GrowCard(ProbeWidget())
    card.grow((20, 20, 40, 24), (200, 120, 340, 220))
    step(card._scalar)


def drive_fade(token_ms):
    widget = ProbeWidget()
    widget.set_opacity(0.0)
    nbmotion.fade_to(widget, 1.0, token_ms, nbmotion.EASE_OUT)
    step(getattr(widget, "_nbmotion_opacity", None))


def drive_fade_out(token_ms):
    """The DEPARTURE half — nbapp._close_fade takes the window's overlay from
    opaque to clear on DEPART easing and destroys on landing. Driven as the
    primitive it is, opposite in direction to drive_fade, so the pair measures
    an arrival and a departure rather than the same run twice."""
    widget = ProbeWidget()
    widget.set_opacity(1.0)
    nbmotion.fade_to(widget, 0.0, token_ms, nbmotion.DEPART)
    step(getattr(widget, "_nbmotion_opacity", None))


def drive_damaged_arrival(token_ms):
    widget = ProbeWidget()
    motion = nbmotion.Damaged(widget=widget,
                              rect_for=lambda _v: (0, 0, 40, 40),
                              on_frame=lambda _v: None, duration=token_ms,
                              easing=nbmotion.ARRIVE)
    motion.animate_to(1.0)
    step(motion)


def drive_damaged_departure(token_ms):
    widget = ProbeWidget()
    motion = nbmotion.Damaged(widget=widget,
                              rect_for=lambda _v: (0, 0, 40, 40),
                              on_frame=lambda _v: None,
                              duration=nbmotion.SURFACE_IN,
                              easing=nbmotion.ARRIVE)
    motion.jump_to(1.0)
    motion.animate_to(0.0, duration=token_ms, easing=nbmotion.DEPART)
    step(motion)


def drive_finder_nav(_token_ms):
    import finder
    obj = SimpleNamespace(_nav_gen=0, _nav_slide=None, _nav_v=0.0,
                          _nav_da=ProbeWidget())
    finder.Finder._start_nav_slide(obj, (object(), 40, 40), -1.0)
    driver = nbmotion._DRIVERS.get(id(obj._nav_da))
    step(driver.anims[0] if driver and driver.anims else None)


def drive_finder_view(_token_ms):
    import finder
    obj = SimpleNamespace(_view="grid", _grid_sw=ProbeWidget(),
                          _list_sw=ProbeWidget())
    finder.Finder._apply_view(obj, animate=True)
    step(getattr(obj._grid_sw, "_nbmotion_opacity", None))


def drive_finder_search(_token_ms):
    import finder
    obj = SimpleNamespace(_view="list", _grid_sw=ProbeWidget(),
                          _list_sw=ProbeWidget())
    finder.Finder._settle_search_results(obj)
    step(getattr(obj._list_sw, "_nbmotion_opacity", None))


def drive_finder_empty(_token_ms):
    import finder
    label = ProbeWidget()
    obj = SimpleNamespace(_empty_label=label, _filter="", rel="",
                          search=SimpleNamespace(get_text=lambda: ""))
    finder.Finder._update_empty_state(obj, 0)
    step(getattr(label, "_nbmotion_opacity", None))


def drive_zoom(_token_ms):
    import sequencer
    obj = SimpleNamespace(
        zoom=1.0, view_start=0.0, length=60.0, stack=ProbeWidget(), lanes=[],
        _view_moving=False, _view_target_start=None, _view_target_zoom=None,
        view_span=lambda: 60.0 / obj.zoom,
        _clamp_view=lambda: None, sync_ruler=lambda: None,
        _after_view_change=lambda: None)
    sequencer.Sequencer._animate_view(obj, 2.0, 0.0)
    step(obj._view_anim)


def drive_2048(_token_ms):
    import g2048
    obj = SimpleNamespace(_wells=object(), _anim=None,
                          anim_layer=ProbeWidget(), _anim_frame=lambda _v: None)
    obj._begin_settle = lambda ok: g2048.Game2048._begin_settle(obj, ok)
    obj._anim_end = lambda ok: g2048.Game2048._anim_end(obj, ok)
    g2048.Game2048._animate_move(obj, [], [], [], (0, 0, 2))
    first = obj._anim
    step(first)
    step(first)


def drive_splash_lift(_token_ms):
    """The boot handover's own half: `Splash._finish` arms the 180ms quit
    deadline and then lifts the toplevel 32px on PAGE with DEPART.  The real
    method is called, so what is measured is the code boot runs — including the
    ordering that lets the handover happen whether or not the lift works.  The
    desktop half of this entry is a different PROCESS and is not drivable from
    anywhere in this harness."""
    import splash
    win = ProbeToplevel()
    win._done = False
    win._fraction = 0.0
    win.bar = ProbeWidget()
    splash.Splash._finish(win)
    driver = nbmotion._DRIVERS.get(id(win))
    step(driver.anims[0] if driver and driver.anims else None)


def drive_picker_arrive(_token_ms):
    """The picker's declared deviation from G3's grow-from-the-menu-item: the
    dialog's own body settles 12px in on SURFACE_IN with ARRIVE.  `_arrive` IS
    that motion end to end and takes only the body widget, so the app's method
    runs untouched."""
    import nbpicker
    obj = SimpleNamespace()
    nbpicker._Picker._arrive(obj, ProbeDrawable())
    step(getattr(obj, "_arrival_motion", None))


def drive_video_selection(_token_ms):
    """Video's clip acknowledgement: the newly selected clip settles from 0.72
    to full on SELECT.  ONE story cell and no lane cells, because the entry is
    about the acknowledgement's pace and a fixture with n cells would drain n
    identical runs and sum them into a false total."""
    import video
    cell = ProbeWidget()
    obj = SimpleNamespace(_story_cells=[cell], _timeline_clip_cells={})
    video.VideoEditor._animate_clip_selection(obj, 0)
    driver = nbmotion._DRIVERS.get(id(cell))
    step(driver.anims[0] if driver and driver.anims else None)


def drive_maps_view(_token_ms):
    """Maps tweens the RENDERER's viewport (cx/cy/scale) on PAGE with ARRIVE —
    it is not a widget container.  The cached raster is pre-seeded to match the
    starting scale so `_animate_view` takes its normal path and the measurement
    is the tween, never a vector re-render."""
    import maps
    canvas = ProbeCanvas()
    obj = SimpleNamespace(
        pack=object(), canvas=canvas,
        cx=10.0, cy=20.0, scale=1000.0,
        _surface=object(), _surf_size=(canvas.get_allocated_width(),
                                       canvas.get_allocated_height()),
        _surf_scale=1000.0, _surf_dev=1, _surf_cx=10.0, _surf_cy=20.0,
        _view_gen=0, _view_anim=None, _view_moving=False,
        _render_surface=lambda *_a: None,
        _invalidate=lambda: None, _save_cfg=lambda: None)
    maps.Maps._animate_view(obj, 12.0, 22.0, 1400.0)
    step(obj._view_anim)


def drive_media_surface(_token_ms):
    """Media's stage swap (empty -> image): the stage departs, the surfaces are
    exchanged while it is invisible, and the new one arrives.  Both halves run
    on the SAME stage Scalar — the arrival is started by the departure's
    completion callback — so the second step drives it, exactly as the shared
    `replace` primitive is driven above."""
    import media
    stage = ProbeWidget()
    obj = SimpleNamespace(
        _empty=ProbeSurface(True), _scroll=ProbeSurface(False),
        _video=ProbeSurface(False), _notice=ProbeSurface(False),
        _stage=stage, _surface_name="empty", _surface_gen=0,
        _stage_full=False)
    media.MediaViewer._show_surface(obj, "image")
    first = getattr(stage, "_nbmotion_opacity", None)
    step(first)
    step(first)


def drive_ebook_chapter(_token_ms):
    """The e-book page/chapter turn: ONE container replacement of the document
    column on PAGE.

    The column and header BUILDERS are fixtured, and that is a deliberate
    limit, not a shortcut: they construct real Gtk widgets, and a driver that
    constructs a Gtk widget aborts this process outright on a machine with no
    display (`Gtk-ERROR: Can't create a GtkStyleContext without a display
    connection` is not catchable), so it would not degrade — it would take the
    whole gate down.  Everything the entry is about still comes from the app:
    `_epub_show_chapter`'s `to_top` gate, the holder it finds through the old
    column's parent, the PAGE token and its pack callback."""
    import ebook
    holder = ProbeBox()
    old = ProbeBox()
    holder.add(old)
    obj = SimpleNamespace(
        _epub_col=old, _epub_pages=[(0, 0, None)], _page=0,
        _epub_chapters=[[]],
        _read_pt=ebook.EbookReader.READ_PT_DEFAULT,
        READ_PT_DEFAULT=ebook.EbookReader.READ_PT_DEFAULT,
        _epub_scroll=None, _scroll_top=lambda *_a: False,
        _nav=SimpleNamespace(guard=lambda fn: fn),
        _new_epub_column=ProbeBox,
        _epub_chapter_header=lambda *_a, **_k: (ProbeWidget(), []))
    ebook.EbookReader._epub_show_chapter(obj)
    first = getattr(holder, "_nbmotion_opacity", None)
    step(first)
    step(first)


DRIVERS = {
    "system.app-launch": drive_fade,
    "system.app-close": drive_fade_out,
    "system.panel-menu-open": drive_damaged_arrival,
    "system.panel-menu-close": drive_damaged_departure,
    "finder.navigate-forward": drive_finder_nav,
    "finder.navigate-back": drive_finder_nav,
    "finder.open-folder": drive_finder_nav,
    "finder.list-grid": drive_finder_view,
    "finder.search-results": drive_finder_search,
    "finder.empty-populated": drive_finder_empty,
    "finder.get-info": drive_overlay,
    "app.overlay-card": drive_overlay,
    "app.confirm": drive_overlay,
    "app.about": drive_overlay,
    "app.tab-section-change": drive_replace,
    "app.any-value-change": drive_replace,
    "app.progress": drive_progress,
    "app.empty-populated": drive_replace,
    "app.zoom": drive_zoom,
    "content.2048": drive_2048,
    "system.splash-desktop": drive_splash_lift,
    "app.picker": drive_picker_arrive,
    "content.video": drive_video_selection,
    "content.maps": drive_maps_view,
    "content.media": drive_media_surface,
    "content.ebook": drive_ebook_chapter,
}

DRIVER_TOKEN = {"app.progress": "FEEDBACK",
                "app.tab-section-change": "PAGE",
                # The ONE motion bound to content.illustrator is the layer-row
                # disclosure (illustrator.py's only two motion calls are
                # nbtransitions.reveal at SURFACE_IN / SURFACE_OUT).  The
                # entry's tokens come from G4's per-app row, `FEEDBACK /
                # SELECT`, which covers the tool change and the reorder — and
                # neither of those was built.  G3 tokens a row opening
                # SURFACE_IN, so that is the token the driven primitive uses
                # and the band it is answerable to.  Naming it here rather than
                # in the entry keeps the inventory's declaration intact: the
                # gap between what G4 asks for and what exists is real and is
                # why the entry is still `partial`.
                "content.illustrator": "SURFACE_IN"}


CSS_IDS = {"app.inline-edit-begin-end", "app.toolbar-state", "app.any-toggle"}
GTK_NATIVE_IDS = {"app.page-pane-switch", "app.list-insert", "app.list-remove"}
#: App transitions a Gtk.Revealer owns, and the direction the app opens them
#: in.  There is no driver to write for these, and that is a fact about the
#: code rather than about this harness: the app's only motion call is
#: `nbtransitions.reveal`, GTK then animates the child in C, and no nbmotion
#: Scalar — so no trace — exists at any point in the transition.
#:
#: What can still be read is the token the APP declares at its own marked call
#: site, resolved through the shared helper into the duration GTK is handed.
#: Re-typing the token here instead (what app.list-insert does, correctly —
#: that entry BINDS to nbtransitions.reveal, so the helper is its
#: implementation) would make these two rows unable to fail for any change to
#: finder.py or illustrator.py, which is a decoration, not a check.
REVEALER_APP_IDS = {"finder.sidebar-reveal": nbtransitions.SLIDE_DOWN,
                    "content.illustrator": nbtransitions.SLIDE_DOWN}


def marker_call_duration(entry, attr="reveal"):
    """The duration the app declares at the `nbmotion-inventory` marker.

    Read from the module's AST, not executed, and the reason is a hard one:
    both call sites sit inside methods that construct real Gtk widgets, and
    constructing a Gtk widget with no display connection ABORTS the process
    (`Gtk-ERROR: Can't create a GtkStyleContext without a display connection`
    is not a Python exception and cannot be caught), so an executing driver
    would not degrade on a headless machine — it would take the whole gate
    down with it.

    The module is resolved under DE, so `NB_DE_DIR` red-proofs a sabotaged copy
    exactly as it does for a driven transition.  Only a duration token or an
    integer literal is accepted: anything else is reported as an unreadable
    configuration rather than quietly passing.
    """
    import ast
    module = DE / Path(entry["binding"]["module"]).name
    source = module.read_text(encoding="utf-8")
    needle = "nbmotion-inventory: %s" % entry["id"]
    lines = source.splitlines()
    marked = [i + 1 for i, line in enumerate(lines)
              if needle in line and line.lstrip().startswith("#")]
    if len(marked) != 1:
        raise ValueError("%s carries %d '%s' markers, expected 1"
                         % (module.name, len(marked), needle))
    calls = [node for node in ast.walk(ast.parse(source, filename=str(module)))
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute)
             and node.func.attr == attr
             and isinstance(node.func.value, ast.Name)
             and node.func.value.id == "nbtransitions"
             and node.lineno > marked[0]]
    if not calls:
        raise ValueError("no nbtransitions.%s call after the %s marker in %s"
                         % (attr, entry["id"], module.name))
    call = min(calls, key=lambda node: node.lineno)
    declared = [kw.value for kw in call.keywords if kw.arg == "duration"]
    if len(declared) != 1:
        raise ValueError("%s:%d passes no single duration= to %s"
                         % (module.name, call.lineno, attr))
    value = declared[0]
    if isinstance(value, ast.Constant) and isinstance(value.value, int):
        return int(value.value), "%s:%d" % (module.name, call.lineno)
    if (isinstance(value, ast.Attribute)
            and isinstance(value.value, ast.Name)
            and value.value.id in ("nbtransitions", "nbmotion")
            and value.attr in TOKEN_MS):
        return TOKEN_MS[value.attr], "%s:%d" % (module.name, call.lineno)
    raise ValueError("%s:%d duration is neither a token nor a literal"
                     % (module.name, call.lineno))
#: The shipped theme.  `NB_THEME_CSS` overrides it for the same reason
#: `NB_DE_DIR` overrides the runtime modules: a theme-side check that cannot be
#: pointed at a sabotaged copy cannot be red-proved, and the alternative --
#: editing the shipped theme in place during a proof -- is how a mutation gets
#: left behind in a release tree.
CSS = Path(os.environ.get(
    "NB_THEME_CSS",
    ROOT / ("buildroot/board/notebookos/rootfs-overlay/usr/share/themes/"
            "Papertone/gtk-3.0/gtk.css"))).resolve()

#: Transitions the THEME performs, checked through GTK's own CSS parser against
#: the selector the entry BINDS to.  Keyed by id rather than inferred, so the
#: pairing is readable here; the selector itself is read from the entry.
CSS_SELECTOR_IDS = {"finder.selection-change"}


def css_selector_duration(entry):
    """The transition duration GTK itself resolves for this entry's selector.

    NOT a regex over the theme source.  The file is handed to a
    `Gtk.CssProvider` and the rule is read back out of the PARSED form, so a
    declaration GTK rejects, drops, or never associates with this selector
    cannot pass: the check sees what the toolkit sees.  Verified
    display-independent before being relied on here — `load_from_path` and
    `to_string` both work with DISPLAY unset, where constructing so much as a
    Gtk.Box aborts the process outright.

    What it does NOT establish is that any frame was rendered, and for a
    TreeView specifically it does not establish that a per-ROW selection change
    eases: rows are painted by cell renderers through a `gtk_style_context_save`
    context inside the TreeView's draw, not by a state change on the node this
    rule matches.  That distinction is the entry's note, and it is why this
    returns a `configured-verified` row and never a `measured` one.
    """
    from gi.repository import Gtk
    selector = entry["binding"]["symbol_or_marker"].strip().rstrip(",").strip()
    provider = Gtk.CssProvider()
    errors = []
    provider.connect("parsing-error",
                     lambda _p, _sec, err: errors.append(str(err)))
    provider.load_from_path(str(CSS))
    if errors:
        raise ValueError("GTK reported %d parse error(s) in %s: %s"
                         % (len(errors), CSS.name, errors[0]))
    found = []
    for block in provider.to_string().split("}"):
        if "{" not in block:
            continue
        head, body = block.split("{", 1)
        if selector not in [s.strip() for s in head.replace("\n", " ").split(",")]:
            continue
        for line in body.splitlines():
            line = line.strip().rstrip(";")
            if line.startswith("transition-duration:"):
                found.append(line.split(":", 1)[1].strip())
    if not found:
        raise ValueError(
            "GTK's parse of %s carries no transition-duration for %r — the "
            "selector is not in a rule that declares one"
            % (CSS.name, selector))
    # Last one wins, which is what the cascade does with two matching rules.
    text = found[-1]
    if text.endswith("ms"):
        ms = float(text[:-2])
    elif text.endswith("s"):
        ms = float(text[:-1]) * 1000.0
    else:
        raise ValueError("unparsable transition-duration %r for %r"
                         % (text, selector))
    return ms, "%s rule in GTK's parse of %s" % (selector, CSS.name)


def configured_measure(entry):
    """Read the owner engine's configuration, never call it a frame measure.

    This proves the transition was handed an in-band duration.  It does not
    prove GTK rendered frames, their cadence, or elapsed wall-clock pacing.
    """
    ident = entry["id"]
    if (ident not in GTK_NATIVE_IDS and ident not in CSS_IDS
            and ident not in REVEALER_APP_IDS
            and ident not in CSS_SELECTOR_IDS):
        return None
    token_const = DRIVER_TOKEN.get(ident) or expected_token(entry)
    token = TARGET[token_const]
    lo, hi = nbmotion.DURATION_BANDS[token]
    if ident in GTK_NATIVE_IDS or ident in REVEALER_APP_IDS:
        class TransitionRecorder:
            """The GTK setter protocol, without constructing a display widget."""
            def set_transition_duration(self, value): self.duration = value
            def get_transition_duration(self): return self.duration
            def set_transition_type(self, value): self.transition_type = value
            def set_visible_child_name(self, value): self.child = value
            def set_reveal_child(self, value): self.revealed = value

        if ident in REVEALER_APP_IDS:
            widget = TransitionRecorder()
            declared, site = marker_call_duration(entry)
            returned = nbtransitions.reveal(
                widget, True, direction=REVEALER_APP_IDS[ident],
                duration=declared)
        elif ident == "app.page-pane-switch":
            widget = TransitionRecorder()
            returned = nbtransitions.switch_page(
                widget, "b", nbtransitions.FORWARD, TOKEN_MS[token_const])
        else:
            widget = TransitionRecorder()
            opening = ident == "app.list-insert"
            direction = (nbtransitions.SLIDE_DOWN if opening
                         else nbtransitions.SLIDE_UP)
            _kind, returned = nbtransitions.revealer_plan(
                direction, TOKEN_MS[token_const])
            widget.set_transition_duration(returned)
        configured = widget.get_transition_duration()
        source = "shared-helper setter capture"
        if ident in REVEALER_APP_IDS:
            source = "app call-site token, resolved through the shared helper"
        if int(returned) != int(configured):
            reason = "helper returned %sms but GTK retained %sms" % (
                returned, configured)
            verdict = "fail"
        elif ident in REVEALER_APP_IDS:
            reason = ("%s declares an in-band token and the helper hands GTK "
                      "that duration; does not prove GTK rendered frames, "
                      "their cadence, or elapsed time" % site)
            verdict = "pass"
        else:
            reason = ("helper handed its owner an in-band duration; does not "
                      "prove GTK retained it or rendered frames/cadence/time")
            verdict = "pass"
    elif ident in CSS_SELECTOR_IDS:
        configured, source = css_selector_duration(entry)
        reason = ("GTK's own parser keeps this duration on the bound selector "
                  "and it is in band; does not prove a frame was rendered, nor "
                  "that a per-row selection change eases (see the entry note)")
        verdict = "pass"
    elif ident in CSS_IDS:
        import re
        text = CSS.read_text(encoding="utf-8")
        marker = "nbmotion-inventory: app.toolbar-state"
        pos = text.index(marker)
        block = text[pos:text.index("}", pos) + 1]
        found = re.search(r"transition-duration:\s*([0-9]+)ms", block)
        if found is None:
            raise ValueError("toolbar-state CSS block has no millisecond duration")
        configured = int(found.group(1))
        source = "theme source declaration parse"
        reason = ("declared CSS duration in band; does not prove theme loading, "
                  "rendered frames, cadence, or elapsed time")
        verdict = "pass"
    else:
        return None
    if not (lo <= round(configured) <= hi):
        verdict = "fail"
        reason = "configured %sms outside band %d-%dms" % (configured, lo, hi)
    return {"status": "configured-verified", "mode": source,
            "token": token, "configured_duration_ms": configured,
            "band_ms": [lo, hi], "verdict": verdict, "reason": reason}


def expected_token(entry):
    """Select the configured token this representative primitive run uses."""
    choices = [t for t in entry["tokens"] if t in TARGET]
    if not choices:
        return None
    # Multi-token entries need an explicit driver choice (tab replacement uses
    # PAGE; the SELECT token describes the control's separate selection cue).
    return choices[0]


def trace_stats(traces):
    frames = 0
    longest = 0.0
    total = 0.0
    run_tokens = []
    for tr in traces:
        run_tokens.append(float(tr[0]))
        times = list(tr[1:])
        frames += max(0, len(tr) - 2)
        gaps = [(times[i] - times[i - 1]) * 1000.0
                for i in range(1, len(times))]
        longest = max([longest] + gaps)
        if len(times) >= 2:
            total += (times[-1] - times[0]) * 1000.0
    return frames, longest, total, run_tokens


#: Motion with no discrete run to time — each reason names the mechanism that
#: makes it so, and each is checkable against the module named in the entry.
CONTINUOUS_REASONS = {
    "content.sequencer":
        "linear playhead follows the audio/timer continuously; it creates no "
        "nbmotion run",
    "system.boot-session":
        "indeterminate progress, not a transition: splash._tick_bar is a 70ms "
        "GLib timer easing the fill toward a 0.9 CAP it deliberately never "
        "passes ('+= (0.9 - self._fraction) * 0.08 + 0.003'), and it stops the "
        "timer on reaching it. It starts no nbmotion run, and it has no "
        "end-to-end length to band: it runs for however long the session takes "
        "to come up, bounded only by the 30s failsafe. That is why the entry "
        "carries `linear` and no duration token — one cannot be assigned "
        "without inventing an end for it",
}

def unavailable(entry):
    ident = entry["id"]
    if ident in CONTINUOUS_REASONS:
        return {"status": "continuous-untraced",
                "reason": CONTINUOUS_REASONS[ident]}
    if ident in CSS_IDS:
        return {"status": "not-driven", "reason":
                "CSS transition is driven by GTK and emits no nbmotion trace"}
    if ident in GTK_NATIVE_IDS:
        return {"status": "not-driven", "reason":
                "Gtk.Stack/Gtk.Revealer owns the frames and emits no nbmotion trace"}
    token = expected_token(entry)
    if token is None:
        return {"status": "not-driven", "reason":
                "inventory declares linear motion without a duration token/band"}
    return {"status": "not-driven", "reason":
            "no transition-specific driver has been implemented yet"}


def answered(result):
    """Is this row a real answer about pacing, or is it a gap?

    Mirrors `tools/motion_inventory_check.PACING_ANSWERED` — and carries the
    verdict check that gate turned out to need as well.  A `configured-verified`
    row whose configured duration is OUTSIDE its band is a FAILURE and must
    never read as an answer; that hole is the reason an out-of-band reveal could
    sit in the ledger under a green run.  `measured` is listed for completeness
    only: a failing measured verdict turns the run red before this is consulted.

    `not-driven`, `undrivable-headless` and `configuration-unreadable` are the
    three that keep coverage incomplete, and they are the only three.
    """
    status = result["status"]
    if status == "continuous-untraced":
        return True
    if status in ("measured", "configured-verified"):
        return result.get("verdict") == "pass"
    return False


def measure(entry):
    ident = entry["id"]
    driver = DRIVERS.get(ident)
    if driver is None:
        try:
            alternative = configured_measure(entry)
        except Exception as exc:
            return {"status": "configuration-unreadable", "reason":
                    "%s: %s" % (type(exc).__name__, exc)}
        if alternative is not None:
            return alternative
        return unavailable(entry)
    token_const = DRIVER_TOKEN.get(ident) or expected_token(entry)
    token = TARGET[token_const]
    token_ms = TOKEN_MS[token_const]
    nbmotion.trace_drain()
    try:
        driver(token_ms)
    except Exception as exc:  # an inability to construct is coverage, not data
        nbmotion.trace_drain()
        return {"status": "undrivable-headless", "reason":
                "%s: %s" % (type(exc).__name__, exc)}
    traces = nbmotion.trace_drain()
    frames, longest, total, run_tokens = trace_stats(traces)
    lo, hi = nbmotion.DURATION_BANDS[token]
    rounded = round(total)
    failures = []
    # A transition is one of two shapes, and the difference decides what the
    # band means. `replace` SPLITS one token across two halves, so the halves
    # must SUM to it (2 x 100ms = PAGE). g2048's move is a SEQUENCE of full-token
    # beats — a 90ms slide, then a 90ms settle — so summing them condemns a
    # transition whose every beat conforms. The inventory says which it is;
    # nothing about a drained trace can tell them apart.
    beats = entry.get("pacing_beats")
    per_run = [round((list(tr[1:])[-1] - list(tr[1:])[0]) * 1000.0, 3)
               for tr in traces if len(tr) >= 3]
    if not traces or frames == 0:
        failures.append("policy animated but recorded ZERO frames (vacuous)")
    elif beats:
        if len(traces) != beats:
            failures.append("declares %d beats, recorded %d"
                            % (beats, len(traces)))
        for i, ms in enumerate(per_run):
            if not (lo <= round(ms) <= hi):
                failures.append("beat %d of %d is %.1fms, outside band %d-%dms"
                                % (i + 1, len(per_run), ms, lo, hi))
    elif not (lo <= rounded <= hi):
        failures.append("total %.1fms outside band %d-%dms" % (total, lo, hi))
    return {
        "status": "measured", "mode": "offscreen-stepped",
        "token": token, "token_ms": token_ms,
        "trace_run_token_ms": [round(x, 3) for x in run_tokens],
        "trace_runs": len(traces), "frame_count": frames,
        "longest_gap_ms": round(longest, 3),
        "total_duration_ms": round(total, 3), "band_ms": [lo, hi],
        "gap_verdict": "advisory",
        "verdict": "fail" if failures else "pass",
        "reason": "; ".join(failures) if failures else ("each of %d beats in band; non-vacuous" % len(per_run) if beats else "total in band; non-vacuous"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write results to the inventory (report-only by default)")
    parser.add_argument("--inventory", type=Path, default=INVENTORY)
    parser.add_argument("--only", action="append", default=[], metavar="ID",
                        help="measure only this entry (repeatable; red-proof aid)")
    args = parser.parse_args()
    data = json.loads(args.inventory.read_text(encoding="utf-8"))
    eligible = [e for e in data["entries"]
                if e["status"] in ("implemented", "partial")]
    if args.only:
        wanted = set(args.only)
        eligible = [e for e in eligible if e["id"] in wanted]
        missing = wanted - {e["id"] for e in eligible}
        if missing:
            parser.error("unknown/ineligible --only: %s" % ", ".join(sorted(missing)))
        if args.apply:
            parser.error("--apply cannot be combined with --only")
    results = {}
    print("transition pacing mode: %s" % MODE)
    print("runtime modules: %s" % DE)
    print("\n  transition                     status                 token       frames  gap_ms duration_ms  verdict")
    for entry in eligible:
        result = measure(entry)
        results[entry["id"]] = result
        if result["status"] == "measured":
            print("  %-30s %-22s %-11s %6d %8.1f %10.1f  %s" %
                  (entry["id"], result["status"], result["token"],
                   result["frame_count"], result["longest_gap_ms"],
                   result["total_duration_ms"], result["verdict"].upper()))
        elif result["status"] == "configured-verified":
            print("  %-30s %-22s %-11s %6s %8s %10.1f  %s" %
                  (entry["id"], result["status"], result["token"], "-", "-",
                   result["configured_duration_ms"], result["verdict"].upper()))
        else:
            print("  %-30s %-22s %-11s %6s %8s %10s  %s" %
                  (entry["id"], result["status"], "-", "-", "-", "-",
                   result["reason"]))

    statuses = ("measured", "configured-verified", "continuous-untraced",
                "configuration-unreadable", "undrivable-headless", "not-driven")
    counts = {s: sum(r["status"] == s for r in results.values())
              for s in statuses}
    # ANY failing verdict, not only a measured one.  Restricted to `measured`,
    # the eight `configured-verified` rows could print FAIL in the table above
    # while this line still said the run was green — and a check that cannot
    # turn the gate red is a decoration.  Red-proved by pointing NB_DE_DIR at a
    # copy of illustrator.py whose reveal asks for 20ms.
    failed = [ident for ident, r in results.items()
              if r.get("verdict") == "fail"]
    unanswered = [entry["id"] for entry in eligible
                  if not answered(results[entry["id"]])]
    print("\nCOVERAGE: " + " ".join("%s=%d" % (s, counts[s]) for s in statuses)
          + " eligible=%d" % len(eligible))
    # Say what the coverage is MADE OF on its own line, because the verdict
    # sentence below cannot: only 24 of these rows carry a real frame trace, and
    # a reader who is told "green" is owed the composition without having to
    # count the table.
    print("ANSWERED: %d of %d — %d measured from a frame trace, %d "
          "configured-verified, %d continuous-untraced; %d unanswered"
          % (len(eligible) - len(unanswered), len(eligible), counts["measured"],
             counts["configured-verified"], counts["continuous-untraced"],
             len(unanswered)))
    if args.apply:
        for entry in eligible:
            entry["pacing"] = results[entry["id"]]
        # Preserve the inventory's established formatting so an apply diff
        # contains MEASUREMENTS and nothing else.  This wrote indent=1 ASCII
        # against a file stored two-space and UTF-8, so every apply rewrote all
        # 1174 lines and buried the numbers it came to record -- the comment
        # said "not whole-file formatting churn" while the code produced
        # exactly that.
        args.inventory.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        print("WROTE: %s" % args.inventory)
    else:
        print("REPORT ONLY: inventory unchanged (use --apply to write)")
    if failed:
        print("RESULT: RED — pacing failures: %s" % ", ".join(failed))
        return 1
    if unanswered:
        # Reported, not failed (exit 0), which is the contract run_all_gates
        # records for this gate — but the sentence is not the accepted green
        # one, so the aggregate still shows the gap rather than swallowing it.
        print("RESULT: MEASURED VERDICTS GREEN; COVERAGE INCOMPLETE — "
              "%d of %d transitions unanswered: %s"
              % (len(unanswered), len(eligible), ", ".join(unanswered)))
    else:
        # The wording is run_all_gates' accepted grammar for this gate and is
        # not free text; the ANSWERED line above carries the composition. Every
        # eligible transition has a pacing answer in its band, and the ten that
        # a Gtk.Revealer, a Gtk.Stack, the theme or a continuous quantity owns
        # cannot have a frame trace at all — no driver can be written for them,
        # so requiring one here would have made this sentence unreachable
        # forever rather than demanding.
        print("RESULT: GREEN — every eligible transition measured in band")
    return 0


if __name__ == "__main__":
    sys.exit(main())
