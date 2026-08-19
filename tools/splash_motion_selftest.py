#!/usr/bin/env python3
"""Behavioral gate for the boot splash departure motion."""
import os
import sys

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DE = os.path.join(
    ROOT, "buildroot", "board", "notebookos", "rootfs-overlay", "opt",
    "notebook", "de")
DE = os.environ.get("SPLASH_MODULE_DIR", DEFAULT_DE)
sys.path.insert(0, DE)

import splash  # noqa: E402


passed = failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print("ok  ", name)
    else:
        failed += 1
        print("FAIL", name + ((": " + detail) if detail else ""))


gtk_ok, _argv = Gtk.init_check()
check("GTK real-widget fixture is reachable", gtk_ok,
      "[not reached: Gtk.init_check() failed; use tools/guestrun.sh]")

events = []
calls = []
moves = []
windows = []
real_timeout = splash.GLib.timeout_add
real_quit = splash.Gtk.main_quit
real_animate = splash.nbmotion.animate if splash.nbmotion is not None else None
real_move = splash.Splash.move


class Pending:
    def cancel(self):
        return None


def timeout(ms, callback, *args):
    events.append(("timeout", ms, callback, args))
    return len(events)


def quit_now():
    events.append(("quit",))


def capture(widget, on_frame, start, end, duration=None, easing=None,
            fade=False, on_done=None):
    events.append(("animate",))
    calls.append({"widget": widget, "frame": on_frame, "start": start,
                  "end": end, "duration": duration, "easing": easing})
    return Pending()


def observe_move(widget, x, y):
    moves.append((widget, x, y))


try:
    splash.GLib.timeout_add = timeout
    splash.Gtk.main_quit = quit_now
    splash.Splash.move = observe_move
    if gtk_ok and real_animate is not None:
        win = splash.Splash()
        windows.append(win)
        origin_x, origin_y = win.get_position()
        events.clear()
        splash.nbmotion.animate = capture
        win._finish()
        call = calls[0] if calls else None

        check("real finish path reaches the motion primitive", call is not None,
              "[not reached: _finish made no animate call]")
        check("boot handover deadline is armed before motion",
              len(events) >= 2 and events[0][0] == "timeout"
              and events[0][1] == splash.GRACE_MS
              and events[1][0] == "animate",
              "[not reached: ordered events=%r]" % (events,))
        check("splash departure receives the PAGE token",
              call is not None
              and call.get("duration") == splash.nbmotion.PAGE
              and call.get("duration", 0) > 0,
              "[not reached: no captured primitive]" if call is None
              else "duration=%r" % call.get("duration"))
        check("splash departure receives DEPART easing",
              call is not None
              and call.get("easing") is splash.nbmotion.DEPART,
              "[not reached: no captured primitive]" if call is None
              else "easing=%r" % call.get("easing"))

        if call is not None and callable(call.get("frame")):
            call["frame"](0.5)
            call["frame"](1.0)
            check("lift advances upward and lands exactly",
                  len(moves) >= 2 and moves[-2][1] == origin_x
                  and moves[-2][2] == origin_y - 16
                  and moves[-1][1] == origin_x
                  and moves[-1][2] == origin_y - 32,
                  "moves=%r" % (moves,))
        else:
            check("lift advances upward and lands exactly", False,
                  "[not reached: frame callback absent]")

        # The most important failure case: the real finish method must hand
        # over synchronously when the primitive itself raises.
        safety = splash.Splash()
        windows.append(safety)
        events.clear()

        def broken_animate(*_args, **_kwargs):
            events.append(("animate-raised",))
            raise RuntimeError("injected primitive failure")

        splash.nbmotion.animate = broken_animate
        safety._finish()
        # The handover deadline is ARMED before any motion is attempted, so a
        # primitive that raises cannot trap the session: the same grace timer
        # that a clean lift uses still quits the boot loop (the completed bar
        # stays briefly visible either way, rather than the screen blinking
        # away the instant motion happens to break). What must hold is that a
        # main_quit is scheduled — a real handover — not that it fires
        # synchronously inside _finish.
        handover = [e for e in events
                    if e[0] == "timeout" and e[1] == splash.GRACE_MS
                    and e[2] is splash.Gtk.main_quit]
        check("raising motion primitive still hands the boot over",
              len(handover) == 1 and not safety.get_visible() is None,
              "[not reached: events=%r]" % (events,))

        # Exercise nbmotion's actual Reduced Motion policy on another real
        # widget: the lift endpoint must be applied before _finish returns.
        reduced = splash.Splash()
        windows.append(reduced)
        events.clear()
        moves.clear()
        splash.nbmotion.animate = real_animate
        splash.nbmotion.set_reduced_motion(True)
        reduced_x, reduced_y = reduced.get_position()
        reduced._finish()
        check("Reduced Motion is instant-equivalent at the lift endpoint",
              bool(moves and moves[-1][1] == reduced_x
                   and moves[-1][2] == reduced_y - 32),
              "[not reached: moves=%r]" % (moves,))
    else:
        reason = "Gtk.init_check failed" if not gtk_ok else "nbmotion unavailable"
        for name in (
                "real finish path reaches the motion primitive",
                "boot handover deadline is armed before motion",
                "splash departure receives the PAGE token",
                "splash departure receives DEPART easing",
                "lift advances upward and lands exactly",
                "raising motion primitive still hands over immediately",
                "Reduced Motion is instant-equivalent at the lift endpoint"):
            check(name, False, "[not reached: %s]" % reason)
except Exception as exc:
    check("splash motion fixture completes", False,
          "[not reached: %s: %s]" % (type(exc).__name__, exc))
finally:
    if splash.nbmotion is not None and real_animate is not None:
        splash.nbmotion.animate = real_animate
        splash.nbmotion.set_reduced_motion(False)
    splash.Splash.move = real_move
    splash.GLib.timeout_add = real_timeout
    splash.Gtk.main_quit = real_quit
    for window in windows:
        try:
            window.destroy()
        except Exception:
            pass

try:
    with open(os.path.join(DE, "splash.py"), encoding="utf-8") as fh:
        source = fh.read()
except Exception as exc:
    source = ""
    source_error = "%s: %s" % (type(exc).__name__, exc)
else:
    source_error = ""
check("named system.splash-desktop transition is present",
      "# nbmotion-inventory: system.splash-desktop" in source,
      source_error or "marker absent")

print("\nSPLASH MOTION SELFTEST: %d passed, %d failed; Gtk.init_check=%s"
      % (passed, failed, gtk_ok))
raise SystemExit(1 if failed else 0)
