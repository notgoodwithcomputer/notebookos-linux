#!/usr/bin/env python3
"""Behavioral gate for Illustrator's identity-scoped layer motion."""
import os
import sys

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DE = os.path.join(
    ROOT, "buildroot", "board", "notebookos", "rootfs-overlay", "opt",
    "notebook", "de")
DE = os.environ.get("ILLUSTRATOR_MODULE_DIR", DEFAULT_DE)
sys.path.insert(0, DE)
import illustrator  # noqa: E402

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
check("GTK fixture path is reachable", gtk_ok,
      "not reached: Gtk.init_check failed; run through tools/guestrun.sh")

calls = []
app = None
real_reveal = illustrator.nbtransitions.reveal
if gtk_ok:
    app = illustrator.Illustrator.__new__(illustrator.Illustrator)
    app.cw = app.ch = 4
    app.layers = [illustrator.Layer("Background", 4, 4, fill_white=True)]
    app.active = 0
    app.next_id = 2
    app.layer_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    app.del_btn = Gtk.Button()
    app.down_btn = Gtk.Button()
    app.up_btn = Gtk.Button()
    # The + is one of the header buttons _rebuild_layers now keeps in step: it
    # goes insensitive at the layer ceiling (MAX_LAYERS), because a layer that
    # cannot be written and read back must not be offered.
    app.add_btn = Gtk.Button()
    app.op_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
    app._op_handler = app.op_scale.connect("value-changed", lambda *_: None)
    app.op_val = Gtk.Label()
    app.canvas = type("Canvas", (), {"queue_draw": lambda self: None})()
    app._push = lambda *_: None
    app._refresh_status = lambda: None
    app._mark_unsaved = lambda: None
    app._flash_save = lambda *_: None

    def observed(revealer, reveal_child, **kwargs):
        calls.append((revealer, reveal_child, kwargs))
        return real_reveal(revealer, reveal_child, **kwargs)

    illustrator.nbtransitions.reveal = observed
    try:
        # Drive the real user-operation methods, not a copied helper fixture.
        app._rebuild_layers()
        app._add_layer()
        add_children = list(app.layer_list.get_children())
        add_revealers = [w for w in add_children if isinstance(w, Gtk.Revealer)]
        add_call = calls[0] if calls else None
        check("layer add reaches the transition primitive", add_call is not None,
              "not reached: _add_layer made no reveal call")
        check("only the added row is wrapped for motion",
              len(add_revealers) == 1 and len(add_children) == 2,
              "not reached: expected one Revealer among two layer rows")
        check("added row opens downward with SURFACE_IN",
              bool(add_call and add_call[1] is True
                   and add_call[2].get("direction") == illustrator.nbtransitions.SLIDE_DOWN
                   and add_call[2].get("duration") is illustrator.nbtransitions.SURFACE_IN),
              "not reached: add arguments absent or dead")
        check("added row reaches its visible end state",
              bool(add_revealers and add_revealers[0].get_reveal_child()),
              "not reached: added Revealer stayed closed")

        calls.clear()
        app._delete_layer()
        del_children = list(app.layer_list.get_children())
        del_revealers = [w for w in del_children if isinstance(w, Gtk.Revealer)]
        del_call = calls[0] if calls else None
        check("layer delete reaches the transition primitive", del_call is not None,
              "not reached: _delete_layer made no reveal call")
        check("only the removed row is wrapped for motion",
              len(del_revealers) == 1,
              "not reached: expected exactly one departing Revealer")
        check("removed row closes upward with SURFACE_OUT",
              bool(del_call and del_call[1] is False
                   and del_call[2].get("direction") == illustrator.nbtransitions.SLIDE_UP
                   and del_call[2].get("duration") is illustrator.nbtransitions.SURFACE_OUT),
              "not reached: delete arguments absent or dead")
        check("removed row reaches its hidden end state",
              bool(del_revealers and not del_revealers[0].get_reveal_child()),
              "not reached: departing Revealer stayed open")
        check("document mutation is not gated on motion",
              len(app.layers) == 1 and app.layers[0].name == "Background")
    except Exception as exc:                                      # named, never crash
        check("real add/delete fixture completes", False,
              "not reached: %s: %s" % (type(exc).__name__, exc))
    finally:
        illustrator.nbtransitions.reveal = real_reveal

try:
    with open(os.path.join(DE, "illustrator.py"), encoding="utf-8") as fh:
        source = fh.read()
except Exception as exc:
    source = ""
    check("Illustrator source is readable", False,
          "not reached: %s: %s" % (type(exc).__name__, exc))
check("transition has its Article G inventory name",
      "# nbmotion-inventory: content.illustrator" in source)

print("\nILLUSTRATOR MOTION SELFTEST: %d passed, %d failed" % (passed, failed))
raise SystemExit(1 if failed else 0)
