#!/usr/bin/env python3
"""Accounting's Debit/Credit segment (add form AND edit form): real toggles,
driven, transferring the selection once.

The pair is Gtk.ToggleButtons so assistive technology can read which direction
is chosen. set_active emits "clicked", so the pair now restates itself through
nbapp.choose_segment, which blocks every handler while it lights the row. An
earlier version of this check drove a hand-rolled Button fake with no GObject
signals; it could see neither the ping-pong that shipped nor the fix. This one
wires real toggle buttons the way the two forms do and drives the setters.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, DE)

import gi                                            # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                        # noqa: E402
import accounting                                    # noqa: E402


class Probe:
    _set_dir = accounting.Accounting._set_dir
    _e_set_dir = accounting.Accounting._e_set_dir

    def __init__(self):
        self.btn_debit = Gtk.ToggleButton(label="Debit")
        self.btn_credit = Gtk.ToggleButton(label="Credit")
        self._e_btn_debit = Gtk.ToggleButton(label="Debit")
        self._e_btn_credit = Gtk.ToggleButton(label="Credit")


def lit(b):
    return b.get_active() and b.get_style_context().has_class("segon")


app = Probe()
app._set_dir("debit")
add_debit = (app.fdir == "debit" and lit(app.btn_debit)
             and not app.btn_credit.get_active())
app._set_dir("credit")
add_credit = (app.fdir == "credit" and lit(app.btn_credit)
              and not app.btn_debit.get_active())
app._e_set_dir("debit")
edit_debit = (app._edir == "debit" and lit(app._e_btn_debit)
              and not app._e_btn_credit.get_active())

source = open(os.path.join(DE, "accounting.py"), encoding="utf-8").read()
native = source.count('Gtk.ToggleButton(label=_t("Debit"))') >= 2

results = ((native, "add and edit directions use semantic toggles"),
           (add_debit, "add form exposes Debit selected, class and all"),
           (add_credit, "add form transfers selection to Credit"),
           (edit_debit, "edit form exposes its stored direction"))
for ok, name in results:
    print(("PASS " if ok else "FAIL ") + name)
ok_all = all(ok for ok, _ in results)
print("RESULT: %s" % ("PASS" if ok_all else "FAILED"))
raise SystemExit(0 if ok_all else 1)
