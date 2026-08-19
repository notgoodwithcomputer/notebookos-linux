#!/usr/bin/env python3
"""Real-use regression drive for Settings, on the real widget tree.

Each check below is a thing a person did with the app and got wrong, driven
the same way (sidebar rows, the real combos, switches and sliders) through
tools/appdrive on an offscreen holder. Every check is named; a check fails by
name, never by crash.

  region confirmation        picking a language shows "<Language> is set…" in
                             that language even when the Keyboard page was
                             built earlier (its dual-layout resync used to
                             overwrite the confirmation on the spot)
  sound blank strip          the Sound page opens with no empty error note
                             taking a line under the title rule, and does not
                             jump when a slider is first moved
  percent readout            the volume readout is a label of its own beside
                             the slider, clear of the knob at 100%
  failed switch save         a preference switch whose save fails goes back
                             OFF and the page says why (it used to paint ON
                             over nothing saved, silently), in the alert red
                             rather than the grey of the notes beside it
  backup re-measure          coming back to Backup measures "What gets copied"
                             again instead of showing the first-view figure

Nothing here touches the host: the mixer's `amixer sset` and the keyboard map
(`_apply_keyboard`) are intercepted; locale.json / settings.json live under
the private NB_DRIVE_HOME_ROOT.

Run under the guest theme:
  NB_DRIVE_HOME_ROOT=<scratch> tools/guestrun.sh python3 tools/settings_realuse_selftest.py
"""
import os
import sys
import shutil
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ.setdefault("NB_DRIVE_HOME_ROOT",
                      tempfile.mkdtemp(prefix="settings-realuse-"))
HOME_ROOT = os.environ["NB_DRIVE_HOME_ROOT"]

import appdrive                                                  # noqa: E402
import cairo                                                      # noqa: E402
from gi.repository import Gtk                                     # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name
          + (("  -- " + detail) if (detail and not cond) else ""))


def fresh(store=None):
    shutil.rmtree(HOME_ROOT, ignore_errors=True)
    os.makedirs(HOME_ROOT, exist_ok=True)
    if store is not None:
        cfg = os.path.join(HOME_ROOT, "settings", ".config", "notebook")
        os.makedirs(cfg, exist_ok=True)
        with open(os.path.join(cfg, "settings.json"), "wb") as fh:
            fh.write(store)
    d = appdrive.Drive("settings")
    # never the developer's keyboard map or mixer
    d.app._apply_keyboard = lambda code: True
    smod = d.mod
    real_run = smod.run

    def run(cmd, timeout=4):
        if cmd and cmd[0] == "amixer" and "sset" in cmd:
            return 0, ""
        return real_run(cmd, timeout)
    smod.run = run
    d._real_run = real_run
    return d


def close(d):
    try:
        d.mod.run = d._real_run
    finally:
        d.close()


def go(d, name):
    dict(d.app._rows)[name].clicked()
    d.pump(0.2)


def label_y(d, text):
    for w in d.walk():
        if (isinstance(w, Gtk.Label) and w.get_visible()
                and w.get_text() == text):
            return w.translate_coordinates(d.child, 0, 0)[-1]
    return None


def visible_texts(d):
    return set(d.texts())


def ink_colours(d, widget, path):
    """Every colour actually painted inside `widget`, from a real render."""
    d.shot(path)
    x, y = widget.translate_coordinates(d.child, 0, 0)[-2:]
    alloc = widget.get_allocation()
    surf = cairo.ImageSurface.create_from_png(path)
    data, stride = surf.get_data(), surf.get_stride()
    out = set()
    for py in range(max(y, 0), min(y + alloc.height, surf.get_height())):
        for px in range(max(x, 0), min(x + alloc.width, surf.get_width())):
            o = py * stride + px * 4
            out.add((data[o + 2], data[o + 1], data[o]))
    return out


# ---- Region & Language: the confirmation survives a built Keyboard page ----
def t_region_confirmation():
    import nbi18n
    d = fresh()
    try:
        app = d.app
        go(d, "Keyboard")               # builds _kbd_note
        go(d, "Region & Language")
        combo, codes = app._region_lang, app._region_lang_codes
        note = app._region_note
        combo.set_active(codes.index("ja"))
        d.pump(0.2)
        cat = nbi18n._load_catalog("ja")
        want = cat.get(d.mod.REGION_SET, d.mod.REGION_SET) % \
            nbi18n.LANG_NAMES.get("ja", "ja")
        check("Region: picking a language after the Keyboard page was built "
              "still shows the confirmation in that language",
              note.get_visible() and note.get_text().startswith(want),
              repr(note.get_text()[:80]))
        # A dual layout: the confirmation keeps its own dual sentence and the
        # Keyboard page's note gains the switch-key sentence.
        combo.set_active(codes.index("ru"))
        d.pump(0.2)
        cat = nbi18n._load_catalog("ru")
        want = cat.get(d.mod.REGION_SET, d.mod.REGION_SET) % \
            nbi18n.LANG_NAMES.get("ru", "ru")
        dual = cat.get(d.mod.KBD_DUAL_NOTE, d.mod.KBD_DUAL_NOTE)
        check("Region: a dual-layout language keeps its confirmation and the "
              "Keyboard note gains the switch-key sentence",
              note.get_text().startswith(want) and dual in note.get_text()
              and app._kbd_note.get_visible()
              and app._kbd_note.get_text() == d.mod._t(d.mod.KBD_DUAL_NOTE),
              "%r / kbd %r" % (note.get_text()[:60],
                               app._kbd_note.get_text()[:60]))
        # and back to English
        combo.set_active(codes.index("en"))
        d.pump(0.2)
        check("Region: picking English afterwards shows its confirmation",
              note.get_text().startswith(d.mod.REGION_SET % "English"),
              repr(note.get_text()[:60]))
    finally:
        close(d)


# ---- Sound: no blank strip, no jump; readout clear of the knob ----
def t_sound_page():
    d = fresh()
    try:
        app = d.app
        go(d, "Sound")
        d.pump(0.2)
        lbl = getattr(app, "_sound_error_label", None)
        check("Sound: the page opens with the empty error note hidden",
              lbl is not None and not lbl.get_visible()
              and lbl.get_no_show_all(),
              "visible=%s no_show_all=%s" % (
                  lbl.get_visible() if lbl else None,
                  lbl.get_no_show_all() if lbl else None))
        # the readout is a separate label, in a fixed column
        row = app._percent_scale(
            Gtk.Adjustment(value=100, lower=0, upper=100, step_increment=5),
            lambda *_: None)
        readout = getattr(row, "readout", None)
        scale = None
        if isinstance(row, Gtk.Container):
            scale = next((c for c in row.get_children()
                          if isinstance(c, Gtk.Scale)), None)
        check("Sound: the percent readout is a label beside the slider, not "
              "drawn by the scale",
              scale is not None and not scale.get_draw_value()
              and isinstance(readout, Gtk.Label)
              and readout.get_text() == "100%",
              "row=%r readout=%r" % (type(row).__name__,
                                    readout.get_text() if readout else None))
        if scale is not None and readout is not None:
            scale.set_value(37)
            check("Sound: the readout follows the slider",
                  readout.get_text() == "37%", repr(readout.get_text()))
        # On a host with a mixer, the real page has sliders: check the pixels'
        # geometry — no shift on the first move, readout clear of the scale.
        scales = [w for w in d.walk()
                  if isinstance(w, Gtk.Scale) and w.get_visible()]
        anchor = next((t for t in ("Speakers and headphones", "Volume")
                       if label_y(d, t) is not None), None)
        if scales and anchor:
            y0 = label_y(d, anchor)
            sc = scales[0]
            sc.set_value(100 if sc.get_value() < 100 else 50)
            d.pump(0.2)
            y1 = label_y(d, anchor)
            check("Sound: the page does not move when a slider is first "
                  "touched", y0 == y1, "%s -> %s" % (y0, y1))
            sc.set_value(100)
            d.pump(0.2)
            sx = sc.translate_coordinates(d.child, 0, 0)[-2]
            sw = sc.get_allocation().width
            ro = getattr(sc.get_parent(), "readout", None)
            rx = (ro.translate_coordinates(d.child, 0, 0)[-2]
                  if ro is not None else None)
            check("Sound: at 100% the readout starts clear of the slider's "
                  "own box",
                  ro is not None and rx >= sx + sw + 8,
                  "scale x=%s w=%s readout x=%s" % (sx, sw, rx))
            widths = set()
            for s in scales:
                ro = getattr(s.get_parent(), "readout", None)
                if ro is not None:
                    widths.add(ro.get_allocation().width)
            check("Sound: every readout takes the same fixed width, so the "
                  "sliders line up in one column",
                  len(widths) == 1, repr(widths))
        else:
            print("skip Sound geometry checks (no mixer on this host)")
    finally:
        close(d)


# ---- Accessibility: a switch whose save fails goes back and says why ----
def t_failed_switch():
    damaged = b'{"last_pane": "Keyboard", "kbd_delay": '
    d = fresh(store=damaged)
    try:
        app = d.app
        go(d, "Accessibility")
        before = visible_texts(d)
        sw = [w for w in d.walk()
              if isinstance(w, Gtk.Switch) and w.get_visible()]
        check("Accessibility: three switches on the page", len(sw) == 3,
              str(len(sw)))
        large = sw[0]
        large.set_active(True)          # what a click ends in
        d.pump(0.3)
        check("Accessibility: a switch whose save failed is drawn OFF again",
              large.get_active() is False and large.get_state() is False
              and app._settings.get("large_text") is None,
              "active=%s state=%s setting=%r" % (
                  large.get_active(), large.get_state(),
                  app._settings.get("large_text")))
        new = visible_texts(d) - before
        check("Accessibility: the page says why the switch did not save",
              any("could not be saved" in t for t in new), repr(sorted(new)))
        lbl = getattr(app, "_pref_error_label", None)
        check("Accessibility: the reason is on the Accessibility page itself",
              lbl is not None and lbl.get_visible()
              and lbl.get_text() == app._save_error,
              repr(lbl.get_text() if lbl else None))
        # ...and says it in the alert red: .setnote and .setwarn are both
        # single-class rules and the note colour is written later, so the
        # class the code adds for "something is wrong" was overridden and the
        # reason was set in the same calm grey as the explanation above it.
        ALERT = (0xC8, 0x34, 0x1E)
        col = lbl.get_style_context().get_color(Gtk.StateFlags.NORMAL) \
            if lbl is not None else None
        resolved = (tuple(round(v * 255) for v in
                          (col.red, col.green, col.blue))
                    if col is not None else None)
        painted = (ink_colours(d, lbl, os.path.join(HOME_ROOT, "a11y.png"))
                   if lbl is not None else set())
        check("Accessibility: the reason is in the alert red, not the grey of "
              "the notes beside it",
              resolved == ALERT and ALERT in painted,
              "resolved=%s alert painted=%s" % (resolved, ALERT in painted))
        cfg = os.path.join(HOME_ROOT, "settings", ".config", "notebook",
                           "settings.json")
        with open(cfg, "rb") as fh:
            check("Accessibility: the damaged store was not overwritten",
                  fh.read() == damaged)
    finally:
        close(d)


# ---- Backup: returning to the pane measures again ----
def t_backup_remeasure():
    d = fresh()
    try:
        app = d.app
        home = d.home
        docs = os.path.join(home, "Documents")
        os.makedirs(docs, exist_ok=True)
        go(d, "Backup")
        d.pump(1.0)
        first = app._bk_files
        for i in range(3):
            with open(os.path.join(docs, "note%d.txt" % i), "w") as fh:
                fh.write("hello " * 1000)
        go(d, "System")
        go(d, "Backup")
        d.pump(1.5)
        check("Backup: coming back to the pane measures What gets copied "
              "again",
              app._bk_files == first + 3
              and app._bk_what_lbl.get_text().startswith("%d file"
                                                         % (first + 3)),
              "first=%s now=%s label=%r" % (first, app._bk_files,
                                            app._bk_what_lbl.get_text()))
    finally:
        close(d)


for fn in (t_region_confirmation, t_sound_page, t_failed_switch,
           t_backup_remeasure):
    try:
        fn()
    except Exception as exc:                                      # noqa: BLE001
        check("%s ran without raising" % fn.__name__, False,
              "%s: %s" % (type(exc).__name__, exc))

bad = [n for n, ok in RESULTS if not ok]
print("RESULT: %s (%d checks, %d failed)" % ("PASS" if not bad else "FAILED",
                                            len(RESULTS), len(bad)))
raise SystemExit(1 if bad else 0)
