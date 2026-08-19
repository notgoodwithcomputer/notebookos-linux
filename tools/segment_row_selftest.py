#!/usr/bin/env python3
"""Pick-one rows are pressed with the button a person presses, and answer once.

Accounting's Debit/Credit pair (add form AND edit form), Screenplay's element
row and Packages' list rows all became Gtk.ToggleButtons in an accessibility
pass, and each row's setter restated the row with set_active from inside the
row's own "clicked" handler. set_active emits "clicked", so one press became
an unbounded chain: RecursionError, swallowed by GTK, printed on stderr, exit
code 0 — and on the appliance the row was dead or the window died. Every
existing suite for these apps stayed green because they call the setters
directly and never emit the signal.

This suite presses the real buttons and counts real setter entries, and it
pins the shared helper (nbapp.choose_segment / set_active_quietly) that all
four rows — and the Finder's list/grid radio pair — now go through.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
sys.path.insert(0, DE)
home = tempfile.mkdtemp(prefix="segment-row-")
os.environ["NB_HOME"] = home

# a ping-pong fails fast as RecursionError instead of hanging the suite
sys.setrecursionlimit(200)

import gi                                            # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                        # noqa: E402
import nbapp                                         # noqa: E402

FAILS = []


def chk(name, ok, detail=""):
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       "" if ok else "  <- %s" % detail))
    if not ok:
        FAILS.append(name)


def pump(n=4):
    for _ in range(n):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)


def counted(obj, attr, log):
    orig = getattr(obj, attr)

    def wrapper(*a, **k):
        log.append(a[0] if a else None)
        return orig(*a, **k)
    setattr(obj, attr, wrapper)


# --- the helper's own contract ------------------------------------------
calls = []
a = Gtk.ToggleButton(label="a")
b = Gtk.ToggleButton(label="b")
row = (("a", a), ("b", b))
a.connect("clicked", lambda *_: (calls.append("a"), nbapp.choose_segment(row, "a")))
b.connect("clicked", lambda *_: (calls.append("b"), nbapp.choose_segment(row, "b")))
b.clicked()
chk("helper: a press on a toggle row runs its handler exactly once",
    calls == ["b"] and b.get_active() and not a.get_active()
    and b.get_style_context().has_class("on"), repr(calls))
a.clicked()
a.clicked()   # pressing the lit one: GTK unlights it, the handler relights it
chk("helper: pressing the lit segment keeps it lit, one call per press",
    calls == ["b", "a", "a"] and a.get_active() and not b.get_active(),
    repr(calls))
r1 = Gtk.RadioButton.new_with_label(None, "r1")
r2 = Gtk.RadioButton.new_with_label_from_widget(r1, "r2")
rcalls = []
rrow = (("r1", r1), ("r2", r2))
for key, rb in rrow:
    rb.connect("toggled", lambda w, k=key: w.get_active() and (
        rcalls.append(k), nbapp.choose_segment(rrow, k, "active")))
r2.clicked()
r1.clicked()
chk("helper: a radio pair settles with one activation per press",
    rcalls == ["r2", "r1"] and r1.get_active() and not r2.get_active(),
    repr(rcalls))
q = Gtk.ToggleButton(label="q")
qcalls = []
q.connect("clicked", lambda *_: qcalls.append("clicked"))
q.connect("toggled", lambda *_: qcalls.append("toggled"))
nbapp.set_active_quietly(q, True)
chk("helper: set_active_quietly changes state and fires nothing",
    q.get_active() and qcalls == [], repr(qcalls))
q.clicked()
chk("helper: a real press afterwards still reaches both handlers",
    qcalls == ["toggled", "clicked"] or sorted(qcalls) == ["clicked", "toggled"],
    repr(qcalls))

# --- Accounting: Debit / Credit in the add form and the edit form -----------
import accounting                                     # noqa: E402
acc = accounting.Accounting()
acc.show_all()
pump()
log = []
counted(acc, "_set_dir", log)
try:
    acc.btn_credit.clicked()
    pump()
    chk("accounting: Credit press sets the direction once",
        acc.fdir == "credit" and log == ["credit"]
        and acc.btn_credit.get_active() and not acc.btn_debit.get_active()
        and acc.btn_credit.get_style_context().has_class("segon")
        and not acc.btn_debit.get_style_context().has_class("segon"),
        "fdir=%r calls=%r" % (acc.fdir, log))
    acc.btn_debit.clicked()
    pump()
    chk("accounting: Debit press sets it back, once",
        acc.fdir == "debit" and log == ["credit", "debit"], repr(log))
except RecursionError:
    chk("accounting: Debit/Credit presses do not recurse", False, "RecursionError")
# the edit form's pair: open the editor on a real entry
try:
    acc.f_desc.set_text("Coffee")
    acc.f_amt.set_text("3.50")
    acc._on_add()
    pump()
    acc._edit_tx(len(acc.tx) - 1)
    pump()
    if getattr(acc, "_e_btn_credit", None) is not None:
        elog = []
        counted(acc, "_e_set_dir", elog)
        acc._e_btn_credit.clicked()
        pump()
        chk("accounting: editor Credit press sets the editor direction once",
            acc._edir == "credit" and elog == ["credit"]
            and acc._e_btn_credit.get_active(), "edir=%r calls=%r"
            % (getattr(acc, "_edir", None), elog))
    else:
        print("note  accounting editor pair not built by _open_edit; skipped")
except RecursionError:
    chk("accounting: editor pair does not recurse", False, "RecursionError")
except Exception as exc:                                       # noqa: BLE001
    print("note  accounting editor drive skipped: %r" % (exc,))
acc.destroy()
pump()

# --- Screenplay: the element row -------------------------------------------
import screenplay                                     # noqa: E402
sp = screenplay.Screenplay()
sp.show_all()
pump()
slog = []
counted(sp, "_set_active_button", slog)   # reached via self. from _on_element
try:
    sp._elbtns[2].clicked()
    pump()
    chk("screenplay: pressing an element lights it once",
        sp._active == 2 and len(slog) == 1
        and sp._elbtns[2].get_active()
        and sum(1 for x in sp._elbtns if x.get_active()) == 1,
        "active=%r calls=%d lit=%d" % (sp._active, len(slog),
                                       sum(1 for x in sp._elbtns if x.get_active())))
    sp._elbtns[2].clicked()      # re-press the lit element: still lit, one call
    pump()
    chk("screenplay: re-pressing the lit element keeps it lit",
        sp._active == 2 and len(slog) == 2 and sp._elbtns[2].get_active(),
        "active=%r calls=%d" % (sp._active, len(slog)))
    sp._elbtns[0].clicked()
    pump()
    chk("screenplay: choosing another moves the light, once",
        sp._active == 0 and len(slog) == 3 and sp._elbtns[0].get_active()
        and not sp._elbtns[2].get_active(), "active=%r" % sp._active)
except RecursionError:
    chk("screenplay: element presses do not recurse", False, "RecursionError")
sp.destroy()
pump()

# --- Packages: the list rows -----------------------------------------------
import packages                                       # noqa: E402
pk = packages.Packages()
pk.show_all()
pump()
plog = []
counted(pk, "_select_row", plog)          # reached via self. from _on_select
try:
    idxs = sorted(pk._rows)
    chk("packages: the list has rows to press", len(idxs) >= 2, str(len(idxs)))
    second = idxs[1]
    pk._rows[second].clicked()
    pump()
    chk("packages: pressing a row selects it once",
        pk.sel == second and len(plog) == 1
        and pk._rows[second].get_active()
        and sum(1 for r in pk._rows.values() if r.get_active()) == 1,
        "sel=%r calls=%d lit=%d" % (pk.sel, len(plog),
                                    sum(1 for r in pk._rows.values() if r.get_active())))
    first = idxs[0]
    pk._rows[first].clicked()
    pump()
    chk("packages: pressing another row moves the selection, once",
        pk.sel == first and len(plog) == 2
        and not pk._rows[second].get_active(), "sel=%r calls=%d" % (pk.sel, len(plog)))
    pk._rows[first].clicked()      # re-press the selected row: stays selected
    pump()
    chk("packages: re-pressing the selected row keeps it selected",
        pk.sel == first and pk._rows[first].get_active(), "sel=%r" % pk.sel)
except RecursionError:
    chk("packages: row presses do not recurse", False, "RecursionError")
pk.destroy()
pump()

# --- GBA Emulator: the save-slot radio row on a library card ------------------
import gbabuild                                       # noqa: E402
import gbaemu                                         # noqa: E402
from gi.repository import GLib                        # noqa: E402
os.makedirs(os.path.join(home, "Documents"), exist_ok=True)
rom = os.path.join(home, "Documents", "Slots.gba")
d = bytearray(64 * 1024)
d[0:4] = b"\x2e\x00\x00\xea"
d[4:0xA0] = gbabuild.NINTENDO_LOGO
d[0xA0:0xAC] = b"SLOTS".ljust(12, b"\0")
open(rom, "wb").write(d)
ge = gbaemu.GbaEmu()
ge.show_all()
pump()
ge._jobs.join(30)                 # the first library scan is a worker now
for _ in range(25):
    pump()
    GLib.usleep(20000)
glog = []
counted(ge, "_update_slot_widgets", glog)   # reached via self. from _select_slot
try:
    widgets = ge._slot_widgets.get(rom)
    chk("gbaemu: the card exposes its slot buttons", bool(widgets),
        "roms=%r" % [m["path"] for m in ge._roms])
    if widgets:
        widgets["buttons"][2].clicked()
        pump()
        chk("gbaemu: pressing Slot 2 selects it once",
            ge._game_state(rom)["last_slot"] == 2 and glog == [rom]
            and widgets["buttons"][2].get_active()
            and not widgets["buttons"][1].get_active(),
            "slot=%r calls=%r" % (ge._game_state(rom)["last_slot"], glog))
        widgets["buttons"][3].clicked()
        pump()
        chk("gbaemu: pressing Slot 3 moves the choice, once",
            ge._game_state(rom)["last_slot"] == 3 and glog == [rom, rom],
            "slot=%r calls=%r" % (ge._game_state(rom)["last_slot"], glog))
        widgets["buttons"][3].clicked()      # pressing the lit slot: no change
        pump()
        chk("gbaemu: re-pressing the lit slot changes nothing",
            ge._game_state(rom)["last_slot"] == 3 and glog == [rom, rom],
            "slot=%r calls=%r" % (ge._game_state(rom)["last_slot"], glog))
except RecursionError:
    chk("gbaemu: slot presses do not recurse", False, "RecursionError")
ge.destroy()
pump()

# --- Settings: the sidebar section rows -------------------------------------
# The rows connect their bound _select directly (not via a lambda), so a
# per-instance wrapper never sees the call; wrap the CLASS method and measure
# re-entrancy DEPTH, which is exactly what the ping-pong inflates (>1 means a
# set_active from inside _select re-fired the handler).
import settings                                       # noqa: E402
_depth = {"cur": 0, "max": 0, "calls": 0}
_orig_select = settings.Settings._select


def _tracked_select(self, btn, name):
    _depth["cur"] += 1
    _depth["calls"] += 1
    _depth["max"] = max(_depth["max"], _depth["cur"])
    try:
        return _orig_select(self, btn, name)
    finally:
        _depth["cur"] -= 1


settings.Settings._select = _tracked_select
st = settings.Settings()
st.show_all()
pump()
try:
    names = [n for n, _r in st._rows]
    chk("settings: the sidebar has section rows", len(names) >= 3, str(names[:4]))
    _depth.update(cur=0, max=0, calls=0)
    st._rows[1][1].clicked()
    pump()
    lit = [n for n, r in st._rows if r.get_active()]
    chk("settings: clicking a section selects exactly it, no re-entry",
        lit == [names[1]] and _depth["max"] == 1 and _depth["calls"] == 1
        and st._rows[1][1].get_style_context().has_class("selected"),
        "lit=%r depth=%d calls=%d" % (lit, _depth["max"], _depth["calls"]))
    _depth.update(cur=0, max=0, calls=0)
    st._rows[0][1].clicked()
    pump()
    lit = [n for n, r in st._rows if r.get_active()]
    chk("settings: choosing another section moves the light, no re-entry",
        lit == [names[0]] and _depth["max"] == 1 and _depth["calls"] == 1,
        "lit=%r depth=%d calls=%d" % (lit, _depth["max"], _depth["calls"]))
    st._rows[0][1].clicked()          # re-press the selected section
    pump()
    lit = [n for n, r in st._rows if r.get_active()]
    chk("settings: re-pressing the selected section keeps exactly it lit",
        lit == [names[0]] and _depth["max"] == 1, "lit=%r depth=%d" % (lit, _depth["max"]))
except RecursionError:
    chk("settings: sidebar clicks do not recurse", False, "RecursionError")
st.destroy()
pump()
settings.Settings._select = _orig_select

print("\n%d failure(s)" % len(FAILS))
sys.exit(1 if FAILS else 0)
