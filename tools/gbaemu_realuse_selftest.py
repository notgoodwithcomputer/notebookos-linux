#!/usr/bin/env python3
"""Real-use checks for the GBA Emulator's library, dialogs and status line.

Driven through tools/appdrive.py: the app's real widget tree in an offscreen
holder at 1024x740 (the smallest panel the OS supports), with the real handlers,
the real menu and the real save-state metadata. What is measured here is what a
person would see -- how many cartridges a row holds, where the first row sits,
what a dialog says and how it is dressed -- because every defect this file was
written for passed every static and logic check the emulator already had.

  tools/guestrun.sh python3 tools/gbaemu_realuse_selftest.py

The geometry and language checks run in CHILD processes (this same file with
$GBAEMU_PROBE set), because the interface language is read once at import and a
grid can only be compared against another grid built the same way.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))

PROBE = os.environ.get("GBAEMU_PROBE", "")
if not PROBE:
    os.environ["NB_LANG"] = "en"

WORK = os.environ.get("GBAEMU_REALUSE_WORK") or tempfile.mkdtemp(
    prefix="gbaemu-realuse-")
os.environ.setdefault("NB_DRIVE_HOME_ROOT", os.path.join(WORK, "drive"))

_GBA_LOGO16 = bytes.fromhex("24ffae51699aa2213d84820a84e409ad")


def write_gba(path, seed=b"\x01", size=4096):
    """A file the app's own rom_problem() accepts as a real cartridge."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    buf = bytearray(seed * size)
    buf[0x00:0x04] = b"\x00\x00\x00\xEA"          # the ARM branch a GBA jumps to
    buf[0x04:0x14] = _GBA_LOGO16
    with open(path, "wb") as fh:
        fh.write(bytes(buf))
    return path


def lay_out_home(home, count, extra_twins=False):
    """A private Home with `count` cartridges in Documents, built BEFORE the
    app is imported (it reads NB_HOME at import)."""
    if os.path.isdir(home):
        shutil.rmtree(home)
    os.makedirs(os.path.join(home, "Documents"), exist_ok=True)
    names = ["Alpha Quest", "Beta Run", "Cavern", "Dune Rider", "Ember",
             "Frost", "Gale", "Hollow", "Ivory"]
    for i, name in enumerate(names[:count]):
        write_gba(os.path.join(home, "Documents", name + ".gba"),
                  seed=bytes([i + 1]))
    if extra_twins:
        # the same cartridge, byte for byte, in two folders
        write_gba(os.path.join(home, "Documents", "Twin.gba"), seed=b"\x40")
        write_gba(os.path.join(home, "Desktop", "Twin-copy.gba"), seed=b"\x40")
    return home


import appdrive                                                  # noqa: E402
import gi                                                        # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib                              # noqa: E402


def catch_dialog(app, response=Gtk.ResponseType.CANCEL, delay=50, tries=120):
    """Arm a watch that finds the modal dialog the next call raises, records
    what it says and how it is laid out, then answers it. Returns a dict that
    is filled in when the dialog appears.

    It KEEPS WATCHING (up to `tries` x `delay`, ~6s) instead of looking once:
    a single 250ms shot missed the dialog on a loaded machine and the run then
    reported an empty heading and no style classes -- a check going red about
    a defect that was not there, which is worse than one that never runs.
    `missing` says the dialog never appeared, so a failure can say so."""
    box = {"tries": 0}

    def look():
        dlg = None
        for w in Gtk.Window.list_toplevels():
            if isinstance(w, Gtk.Dialog) and w.get_visible() and w is not app:
                dlg = w
        if dlg is None:
            box["tries"] += 1
            if box["tries"] >= tries:
                box["missing"] = True
                return False
            return True
        child = dlg.get_child()
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        dlg.check_resize()
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        texts, classes, first_label_at = [], set(), None
        stack = [child]
        while stack:
            w = stack.pop(0)
            classes.update(w.get_style_context().list_classes())
            if isinstance(w, Gtk.Label) and w.get_visible():
                texts.append(w.get_text())
                if first_label_at is None:
                    first_label_at = w.translate_coordinates(child, 0, 0)
                if w.get_style_context().has_class("alerttitle"):
                    box["heading"] = w.get_text()
            if isinstance(w, Gtk.TextView):
                buf = w.get_buffer()
                box["body_text"] = buf.get_text(buf.get_start_iter(),
                                                buf.get_end_iter(), False)
            if isinstance(w, Gtk.Button) and w.get_visible():
                box.setdefault("buttons", []).append(
                    (w.get_label(), " ".join(
                        w.get_style_context().list_classes())))
            if isinstance(w, Gtk.Container):
                stack.extend(w.get_children())
        box["texts"] = texts
        box["classes"] = sorted(classes)
        box["first_label_at"] = first_label_at
        box["title"] = dlg.get_title()
        dlg.response(response)
        return False

    GLib.timeout_add(delay, look)
    return box


# ======================================================================= probe
def probe():
    """Build the library once and print what it measures, as one JSON line."""
    kind, lang, count = PROBE.split(":")
    count = int(count)
    home = os.path.join(WORK, "probe-%s-%s" % (lang, count))
    lay_out_home(home, count)
    d = appdrive.Drive("gbaemu", home=home)
    d.pump(1.5)
    out = {"lang": lang, "count": count}
    flows = d.find(Gtk.FlowBox)
    if flows:
        kids = flows[0].get_children()
        out["percol"] = len({c.get_allocation().x for c in kids})
        out["card_w"] = kids[0].get_child().get_preferred_width().natural_width
        out["first_y"] = kids[0].translate_coordinates(d.child, 0, 0)[1]
    out["notice"] = [w.get_text() for w in d.find(Gtk.Label)
                     if w.get_style_context().has_class("noticebody")]
    caught = catch_dialog(d.app, response=Gtk.ResponseType.CLOSE)
    d.menu_action("File", "Emulator Log")
    d.pump(0.4)
    out["log_missing"] = "texts" not in caught
    out["log_text"] = caught.get("body_text", "")
    out["log_heading"] = caught.get("heading", "")
    out["log_first_label_at"] = caught.get("first_label_at")
    out["log_classes"] = caught.get("classes", [])
    out["log_buttons"] = caught.get("buttons", [])
    d.close()
    print("PROBE " + json.dumps(out))
    return 0


def run_probe(kind, lang, count):
    env = dict(os.environ)
    env["GBAEMU_PROBE"] = "%s:%s:%d" % (kind, lang, count)
    env["NB_LANG"] = lang
    env["GBAEMU_REALUSE_WORK"] = WORK
    env["NB_DRIVE_HOME_ROOT"] = os.path.join(WORK, "drive-%s-%d" % (lang, count))
    res = subprocess.run([sys.executable, os.path.abspath(__file__)], env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         timeout=180)
    for line in res.stdout.decode("utf-8", "replace").splitlines():
        if line.startswith("PROBE "):
            return json.loads(line[6:])
    raise RuntimeError("probe %s/%s printed no measurement:\n%s"
                       % (lang, count, res.stderr.decode("utf-8", "replace")[-800:]))


if PROBE:
    raise SystemExit(probe())

# ====================================================================== checks
FAILED = []
COUNT = 0


def check(name, fn):
    """Every check fails BY NAME, including when it raises: a suite that dies
    on an exception reports nothing about the defect it was written for."""
    global COUNT
    COUNT += 1
    try:
        ok, detail = fn()
    except Exception as exc:                                      # noqa: BLE001
        ok, detail = False, "raised %s: %s" % (type(exc).__name__, exc)
    print(("ok   " if ok else "FAIL ") + name + (("  — %s" % detail)
                                                 if detail else ""))
    if not ok:
        FAILED.append(name)


# ---- the grid: same shape in every language, same place at every size -------
GRIDS = {}
for _lang in ("en", "fr", "es", "ja"):
    GRIDS[_lang] = run_probe("grid", _lang, 7)
SHORT = run_probe("grid", "en", 2)
FULL = GRIDS["en"]


def cards_per_row():
    rows = {k: v.get("percol") for k, v in GRIDS.items()}
    return (len(set(rows.values())) == 1 and rows["en"] == 4,
            "cards per row: " + ", ".join("%s=%s" % kv
                                          for kv in sorted(rows.items())))


check("the library puts the same number of cartridges in a row in every "
      "language", cards_per_row)


def first_row_place():
    return (SHORT.get("first_y") == FULL.get("first_y"),
            "first card at y=%s with 2 games, y=%s with 7"
            % (SHORT.get("first_y"), FULL.get("first_y")))


check("a library of two games starts where a library of seven does",
      first_row_place)


# ---- the two sentences that stayed English in a translated window ----------
def notice_translated():
    en, fr = FULL.get("notice"), GRIDS["fr"].get("notice")
    return (bool(fr) and fr != en,
            "English %r vs French %r" % (en, fr))


check("the notice about the missing emulator is translated", notice_translated)


def log_line_translated():
    en, fr = FULL.get("log_text"), GRIDS["fr"].get("log_text")
    gone = [k for k in ("en", "fr") if GRIDS[k].get("log_missing")]
    if gone:                       # never blame the words for a dialog that
        return (False,             # never opened -- say which run lost it
                "the log dialog never appeared in %s" % ", ".join(gone))
    return (bool(fr) and fr != en, "English %r vs French %r" % (en, fr))


check("the empty emulator log says so in the reader's language",
      log_line_translated)


def log_is_a_card():
    if FULL.get("log_missing"):
        return (False, "the log dialog never appeared")
    padded = (FULL.get("log_first_label_at") or (0, 0))
    return ("emualert" in FULL.get("log_classes", [])
            and FULL.get("log_heading") == "Emulator Log"
            and padded[0] >= 12 and padded[1] >= 12
            and any("emubtn" in cls for _l, cls in FULL.get("log_buttons", [])),
            "heading=%r first text at %s classes=%s"
            % (FULL.get("log_heading"), padded,
               [c for c in FULL.get("log_classes", []) if "emu" in c]))


check("the emulator log opens in the app's own card, with a heading and "
      "padding", log_is_a_card)


# ---- one drive for everything that needs handlers, not geometry ------------
HOME = os.path.join(WORK, "drive-main")
lay_out_home(HOME, 3, extra_twins=True)
TWIN = os.path.join(HOME, "Documents", "Twin.gba")
COPY = os.path.join(HOME, "Desktop", "Twin-copy.gba")
D = appdrive.Drive("gbaemu", home=HOME)
D.pump(1.5)
gbaemu = D.mod


def one_name_for_one_action():
    labels = {b.get_label() for b in D.find(Gtk.Button) if b.get_label()}
    item = [it[0] for it in D.menu("File") if isinstance(it, (tuple, list))
            and it[1] == D.app._request_scan]
    from nbi18n import _t
    wanted = _t(item[0]) if item else None
    return (wanted in labels,
            "menu item %r, buttons %s" % (wanted, sorted(labels)))


check("looking for new games has ONE name in the window", one_name_for_one_action)


def slots_are_chips():
    """The save slots are one chip per number -- what they were before the
    width fix, minus the word. A radio drawn with its indicator puts a filled
    dot beside every number: the blackest ink on a card whose game name should
    carry that weight, and a second statement of what the lit chip already
    says."""
    from nbi18n import _t
    w = D.app._slot_widgets[TWIN]
    modes = [w["buttons"][s].get_mode() for s in (1, 2, 3)]
    labels = [w["buttons"][s].get_label() for s in (1, 2, 3)]
    tips = [w["buttons"][s].get_tooltip_text() for s in (1, 2, 3)]
    return (modes == [False, False, False] and labels == ["1", "2", "3"]
            and tips == [_t("Slot %d") % s for s in (1, 2, 3)],
            "chips %s labelled %s, named %s" % (
                ["dot" if m else "chip" for m in modes], labels, tips))


check("a save slot is a chip carrying its number, named Slot 2 to the pointer",
      slots_are_chips)


def slot_siblings_follow():
    import time as _time
    state = gbaemu.state_path(TWIN, 1)
    os.makedirs(os.path.dirname(state), exist_ok=True)
    with open(state, "wb") as fh:
        fh.write(b"\0" * 16)
    stamp = _time.mktime(_time.strptime("2026-08-15 14:05", "%Y-%m-%d %H:%M"))
    os.utime(state, (stamp, stamp))
    D.app._request_scan()
    D.pump(1.5)

    def card(path):
        w = D.app._slot_widgets[path]
        return ([w["buttons"][s].get_active() for s in (1, 2, 3)],
                w["keys"].get_text(), w["last"].get_text())

    before = card(COPY)
    D.app._slot_widgets[TWIN]["buttons"][3].set_active(True)
    D.pump(0.3)
    after = card(COPY)
    return (card(TWIN) == after and before != after,
            "the other card reads %s" % (after,))


check("choosing a save slot restates every card of the same cartridge",
      slot_siblings_follow)


def saved_time_is_phrased():
    import time as _time
    slot = D.app._game_state(TWIN)["last_slot"]       # whatever is lit now
    state = gbaemu.state_path(TWIN, slot)
    os.makedirs(os.path.dirname(state), exist_ok=True)
    with open(state, "wb") as fh:
        fh.write(b"\0" * 16)
    stamp = _time.mktime(_time.strptime("2026-08-15 14:05", "%Y-%m-%d %H:%M"))
    os.utime(state, (stamp, stamp))
    D.app._request_scan()
    D.pump(1.5)
    text = D.app._slot_widgets[TWIN]["last"].get_text()
    return ("15 Aug 2026" in text
            and not re.search(r"\d{4}-\d{2}-\d{2}", text), "reads %r" % text)


check("the last-saved time is a date phrase, not a machine stamp",
      saved_time_is_phrased)


def stop_prompt_names_the_game():
    class Fake:
        def stop(self):
            pass

        def _finish(self):
            pass

    D.app._session = Fake()
    D.app._active_rom = TWIN
    caught = catch_dialog(D.app, response=Gtk.ResponseType.CANCEL)
    vetoed = D.app._on_delete()
    D.pump(0.3)
    D.app._session = None
    D.app._active_rom = None
    body = " ".join(caught.get("texts", []))
    return ("Twin.gba" in body and "“Game”" not in body and vetoed,
            "says %r" % body)


check("the stop-game prompt names the game it is about to end",
      stop_prompt_names_the_game)


def status_survives_the_rescan():
    import nbgame
    import nbpicker
    fake_core = os.path.join(WORK, "fake-vbam.sh")
    with open(fake_core, "w") as fh:
        fh.write("#!/bin/sh\nsleep 30\n")
    os.chmod(fake_core, 0o755)
    fresh = write_gba(os.path.join(HOME, "Music", "Fresh Game.gba"), seed=b"\x77")

    class Stub:
        def __init__(self, *a, **k):
            pass

        def run(self):
            pass

        def stop(self):
            pass

        def _finish(self):
            pass

    real_session, real_open = nbgame.GameSession, nbpicker.open_file
    D.app._vbam_path = lambda: fake_core
    nbgame.GameSession = Stub
    nbpicker.open_file = lambda *a, **k: fresh
    try:
        D.app._open_rom()
        D.pump(0.1)
        early = D.app._ctrl_label.get_text()
        D.pump(1.6)                       # the background scan lands in here
        late = D.app._ctrl_label.get_text()
    finally:
        nbgame.GameSession, nbpicker.open_file = real_session, real_open
        D.app._session = None
    return ("Fresh Game.gba" in late and late == early,
            "0.1s %r then 1.7s %r" % (early, late))


check("opening a game the library had not seen keeps saying what it did",
      status_survives_the_rescan)


def status_rests_on_the_controllers():
    D.app._flash("Playing Fresh Game.gba — press Ctrl+Esc to exit.")
    D.app._flash("")
    D.pump(0.1)
    return ("ontroller" in D.app._ctrl_label.get_text()
            or "eyboard" in D.app._ctrl_label.get_text(),
            "reads %r" % D.app._ctrl_label.get_text())


check("a cleared message leaves the status line on the controller text, "
      "not blank", status_rests_on_the_controllers)

D.close()
shutil.rmtree(WORK, ignore_errors=True)
print("\n%d checks, %d failed" % (COUNT, len(FAILED)))
for name in FAILED:
    print("  - " + name)
print("RESULT: %s" % ("FAILED" if FAILED else "PASS"))
sys.exit(1 if FAILED else 0)
