#!/usr/bin/env python3
"""Behavioral gate for the picker body arrival (no toplevel is mapped)."""
import os
import sys

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DE = os.path.join(
    ROOT, "buildroot", "board", "notebookos", "rootfs-overlay", "opt",
    "notebook", "de")
DE = os.environ.get("NBPICKER_MODULE_DIR", DEFAULT_DE)
sys.path.insert(0, DE)

import nbpicker  # noqa: E402


passed = failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print("ok  ", name)
    else:
        failed += 1
        print("FAIL", name + ((': ' + detail) if detail else ""))


gtk_ok, _argv = Gtk.init_check()
print("Gtk.init_check(): %s" % gtk_ok)
check("GTK fixture path is reachable", gtk_ok,
      "[not reached: Gtk.init_check failed; run through tools/guestrun.sh]")

calls = []
real_animate = nbpicker.nbmotion.animate


class Pending:
    def cancel(self):
        return None


def capture(widget, on_frame, start, end, duration=None, easing=None,
            fade=False, on_done=None):
    calls.append({"widget": widget, "frame": on_frame, "start": start,
                  "end": end, "duration": duration, "easing": easing,
                  "done": on_done})
    on_frame(start)
    on_frame(end)
    if on_done is not None:
        on_done(True)
    return Pending()


body = None
if gtk_ok:
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    body.add(Gtk.Button(label="Open"))
    picker = nbpicker._Picker.__new__(nbpicker._Picker)
    nbpicker.nbmotion.animate = capture
    try:
        picker._arrive(body)
    except Exception as exc:
        check("real picker arrival method completes", False,
              "[not reached: %s: %s]" % (type(exc).__name__, exc))
    finally:
        nbpicker.nbmotion.animate = real_animate

call = calls[0] if calls else None
check("real picker path reaches the motion primitive", call is not None,
      "[not reached: _arrive made no animate call]")
check("arrival moves the real picker body", call is not None and body is not None
      and call.get("widget") is body and call.get("start") < call.get("end")
      and call.get("end") == 0.0,
      "[not reached: captured body/range absent]" if call is None
      else "widget=%r range=%r..%r" %
      (call.get("widget"), call.get("start"), call.get("end")))
check("picker arrival receives the SURFACE_IN token",
      call is not None
      and call.get("duration") is nbpicker.nbmotion.SURFACE_IN
      and call.get("duration", 0) > 0,
      "[not reached: no captured primitive]" if call is None
      else "duration=%r" % call.get("duration"))
check("picker arrival receives the lively ARRIVE easing",
      call is not None and call.get("easing") is nbpicker.nbmotion.ARRIVE,
      "[not reached: no captured primitive]" if call is None
      else "easing=%r" % call.get("easing"))
check("instant completion leaves the body usable",
      body is not None and len(body.get_children()) == 1
      and body.get_children()[0].get_sensitive(),
      "[not reached: real body/button unavailable]")

# Motion is decoration: a primitive failure must return normally with the real
# content tree intact and interactive.
fallback_body = None
fallback_returned = False
if gtk_ok:
    fallback_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    fallback_button = Gtk.Button(label="Save")
    fallback_body.add(fallback_button)
    fallback_picker = nbpicker._Picker.__new__(nbpicker._Picker)

    def raising(*_args, **_kwargs):
        raise RuntimeError("injected animation failure")

    nbpicker.nbmotion.animate = raising
    try:
        fallback_picker._arrive(fallback_body)
        fallback_returned = True
    except Exception:
        fallback_returned = False
    finally:
        nbpicker.nbmotion.animate = real_animate
check("failed animation still yields usable picker content",
      fallback_returned and fallback_body is not None
      and len(fallback_body.get_children()) == 1
      and fallback_body.get_children()[0].get_sensitive(),
      "[not reached: fallback raised or content was damaged]")

try:
    with open(os.path.join(DE, "nbpicker.py"), encoding="utf-8") as fh:
        source = fh.read()
except Exception as exc:
    source = ""
    source_error = "%s: %s" % (type(exc).__name__, exc)
else:
    source_error = ""
check("named app.picker transition is present",
      "# nbmotion-inventory: app.picker" in source,
      source_error or "marker absent")

print("\nNBPICKER MOTION SELFTEST: %d passed, %d failed" % (passed, failed))
raise SystemExit(1 if failed else 0)
