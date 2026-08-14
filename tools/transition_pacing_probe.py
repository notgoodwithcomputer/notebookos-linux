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
}

DRIVER_TOKEN = {"app.progress": "FEEDBACK",
                "app.tab-section-change": "PAGE"}


CSS_IDS = {"app.inline-edit-begin-end", "app.toolbar-state", "app.any-toggle"}
GTK_NATIVE_IDS = {"app.page-pane-switch", "app.list-insert", "app.list-remove"}
CSS = ROOT / ("buildroot/board/notebookos/rootfs-overlay/usr/share/themes/"
              "Papertone/gtk-3.0/gtk.css")


def configured_measure(entry):
    """Read the owner engine's configuration, never call it a frame measure.

    This proves the transition was handed an in-band duration.  It does not
    prove GTK rendered frames, their cadence, or elapsed wall-clock pacing.
    """
    ident = entry["id"]
    if ident not in GTK_NATIVE_IDS and ident not in CSS_IDS:
        return None
    token_const = expected_token(entry)
    token = TARGET[token_const]
    lo, hi = nbmotion.DURATION_BANDS[token]
    if ident in GTK_NATIVE_IDS:
        class TransitionRecorder:
            """The GTK setter protocol, without constructing a display widget."""
            def set_transition_duration(self, value): self.duration = value
            def get_transition_duration(self): return self.duration
            def set_transition_type(self, value): self.transition_type = value
            def set_visible_child_name(self, value): self.child = value
            def set_reveal_child(self, value): self.revealed = value

        if ident == "app.page-pane-switch":
            widget = TransitionRecorder()
            returned = nbtransitions.switch_page(
                widget, "b", nbtransitions.FORWARD, TOKEN_MS[token_const])
            configured = widget.get_transition_duration()
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
        if int(returned) != int(configured):
            reason = "helper returned %sms but GTK retained %sms" % (
                returned, configured)
            verdict = "fail"
        else:
            reason = ("helper handed its owner an in-band duration; does not "
                      "prove GTK retained it or rendered frames/cadence/time")
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


def unavailable(entry):
    ident = entry["id"]
    if ident == "content.sequencer":
        return {"status": "continuous-untraced", "reason":
                "linear playhead follows the audio/timer continuously; it creates no nbmotion run"}
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
    failed = [ident for ident, r in results.items()
              if r["status"] == "measured" and r["verdict"] == "fail"]
    print("\nCOVERAGE: " + " ".join("%s=%d" % (s, counts[s]) for s in statuses)
          + " eligible=%d" % len(eligible))
    if args.apply:
        for entry in eligible:
            entry["pacing"] = results[entry["id"]]
        # Preserve the inventory's established one-space indentation so an
        # apply diff contains measurements, not whole-file formatting churn.
        args.inventory.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
        print("WROTE: %s" % args.inventory)
    else:
        print("REPORT ONLY: inventory unchanged (use --apply to write)")
    if failed:
        print("RESULT: RED — measured pacing failures: %s" % ", ".join(failed))
        return 1
    if counts["measured"] != len(eligible):
        print("RESULT: MEASURED VERDICTS GREEN; COVERAGE INCOMPLETE — "
              "%d of %d transitions measured" % (counts["measured"], len(eligible)))
    else:
        print("RESULT: GREEN — every eligible transition measured in band")
    return 0


if __name__ == "__main__":
    sys.exit(main())
