#!/usr/bin/env python3
"""Calendar, driven the way a person uses it, at the 1024x740 panel.

Part one: the sidebar toggles. The calendar rows are Gtk.ToggleButtons.
Gtk.ToggleButton.set_active emits "clicked" as well as "toggled", so a clicked
handler that negates its own model and pushes the result back with set_active
runs itself again the moment the button and the model disagree -- and they
disagree after every path that re-shows a hidden calendar without rebuilding
the row (an event filed into it from quick-add, the event dialog, the
Academics mirror, the View menu). One click on the row then ended in
RecursionError. No calendar suite drove the real button;
calendar_accessibility greps source. This one clicks it.

Part two: the consumer-visible defects a real-use drive found (Aug 2026),
each checked by name at the panel size, through the app's own dialogs:

  * an all-day event has a band in the WEEK view, as in the Day view;
  * Add a Shift puts the new Work calendar in the sidebar at once;
  * a six-row month whose busiest day has three events still fits the panel
    (the '+N more' chip costs no row, and the chip stack is measured);
  * a transient status message never shortens the period title (in Greek
    too, where the month title fills the row), keeps clear of the view
    segment, and is not shown as a lone ellipsis;
  * quick add understands "3 january 2020" and shows the year in its readback;
  * All Day greys the Starts / Duration fields visibly;
  * Delete Repeating Event names the event, its count, and reads destructive;
  * a bad Series End Date says what a date looks like; Notes has a frame.

    tools/guestrun.sh python3 tools/calendar_realuse_selftest.py
"""
import os
import sys
import time
import shutil
import tempfile
import subprocess
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["NB_DRIVE_HOME_ROOT"] = tempfile.mkdtemp(prefix="nb-calendar-realuse-")

import appdrive  # noqa: E402
from gi.repository import Gtk, GLib  # noqa: E402
import cairo  # noqa: E402

FAILS = []
COUNT = 0


def check(name, ok, detail=""):
    global COUNT
    COUNT += 1
    print(("PASS " if ok else "FAIL ") + name + (": " + detail if (detail and not ok) else ""))
    if not ok:
        FAILS.append(name)


def cal_toggles(d):
    return [w for w in d.find(Gtk.ToggleButton)
            if "caltoggle" in w.get_style_context().list_classes()]


# ---------------------------------------------------------------- helpers
def walk(root):
    out = [root]
    i = 0
    while i < len(out):
        w = out[i]
        i += 1
        if isinstance(w, Gtk.Container):
            out.extend(w.get_children())
    return out


def labels_in(root, visible=True):
    return [w.get_text() for w in walk(root)
            if isinstance(w, Gtk.Label) and (w.get_visible() or not visible)]


def button_labelled(root, text):
    for b in walk(root):
        if isinstance(b, Gtk.Button) and any(
                isinstance(l, Gtk.Label) and l.get_text() == text for l in walk(b)):
            return b
    return None


def with_class(root, cls, kind=None):
    return [w for w in walk(root)
            if (kind is None or isinstance(w, kind))
            and cls in w.get_style_context().list_classes()]


def run_dialog(action, steps, timeout=20):
    """Run `action` (which blocks in a modal dlg.run()) and hand each NEW
    visible Gtk.Dialog to the next step; a step ends by responding. Returns
    the number of steps that ran. Nested dialogs (an edit dialog whose Delete
    opens a second one) are served in order."""
    # Held as OBJECTS, not ids: a dialog destroyed by an earlier call can
    # hand its id() to the next one Python allocates, and an id-set then
    # filtered the new dialog out as "already there" — a flaky miss.
    before = list(Gtk.Window.list_toplevels())
    state = {"i": 0, "last": 0.0, "done": False}

    def poll():
        if state["done"]:
            return False
        dl = [w for w in Gtk.Window.list_toplevels()
              if not any(w is b for b in before) and isinstance(w, Gtk.Dialog)
              and w.get_visible()]
        if not dl:
            if state["i"] >= len(steps) or time.monotonic() - state["t0"] > 6:
                state["done"] = True
                return False
            return True
        # Each step gets the top-most dialog showing at the time. A step that
        # clicks a button whose handler keeps the dialog up (a validation
        # failure) is followed by a step that sees the SAME dialog again — the
        # app's code after dlg.run() only runs once this callback returns, so
        # what that code did can only be read in the next step.
        if state["i"] < len(steps) and time.monotonic() - state["last"] > 0.25:
            dlg = dl[-1]
            fn = steps[state["i"]]
            state["i"] += 1
            try:
                fn(dlg)
            except Exception as exc:                              # noqa: BLE001
                print("  dialog step raised %r" % (exc,))
                try:
                    dlg.response(Gtk.ResponseType.CANCEL)
                except Exception:                                 # noqa: BLE001
                    pass
            state["last"] = time.monotonic()
        return True
    state["t0"] = time.monotonic()
    GLib.timeout_add(60, poll)
    action()
    appdrive.pump(0.2)
    while not state["done"] and time.monotonic() - state["t0"] < timeout:
        appdrive.pump(0.1)
    return state["i"]


def dialog_pixels(dlg, widget):
    """The bytes of `widget`'s region in a synchronous render of `dlg`."""
    appdrive.pump(0.1)
    w, h = dlg.get_allocated_width(), dlg.get_allocated_height()
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surf)
    cr.set_source_rgb(1, 1, 1)
    cr.paint()
    dlg.draw(cr)
    surf.flush()
    got = widget.translate_coordinates(dlg, 0, 0)
    x, y = got[-2], got[-1]
    a = widget.get_allocation()
    stride = surf.get_stride()
    data = bytes(surf.get_data())
    rows = []
    for yy in range(max(0, y), min(h, y + a.height)):
        rows.append(data[yy * stride + 4 * max(0, x): yy * stride + 4 * min(w, x + a.width)])
    return b"".join(rows)


def top_y(d, w):
    got = w.translate_coordinates(d.child, 0, 0)
    return None if got is None else got[-1]


def top_x(d, w):
    got = w.translate_coordinates(d.child, 0, 0)
    return None if got is None else got[-2]


def quick_add(d, text):
    d.app.quick.grab_focus()
    d.app.quick.set_text("")
    d.type(text)
    appdrive.pump(0.1)
    hint = d.app.quick_hint.get_text()
    d.key("Return")
    appdrive.pump(0.2)
    return hint


def new_event(d, title, all_day=False, repeat_idx=None, extra=None):
    """File > New Event…, filled and added through the real dialog."""
    def step(dlg):
        entries = [w for w in walk(dlg) if isinstance(w, Gtk.Entry)]
        entries[0].set_text(title)
        if all_day:
            for c in walk(dlg):
                if isinstance(c, Gtk.CheckButton) and c.get_label() == "All Day":
                    c.set_active(True)
        if repeat_idx is not None:
            for cb in walk(dlg):
                if isinstance(cb, Gtk.ComboBoxText) and any(
                        "Every day" in r[0] for r in cb.get_model()):
                    cb.set_active(repeat_idx)
        if extra:
            extra(dlg)
        appdrive.pump(0.1)
        # The app WINDOW is offscreen, but a modal dialog is a real mapped
        # window on the developer's display, so a keystroke typed at the desk
        # while this runs lands in whatever it has focused -- and set_text
        # leaves the cursor at 0, so it landed in FRONT of the title
        # ("vGrandma's birthday party"), failing the chip check for a reason
        # that has nothing to do with the app. Written once more with no pump
        # after it, so the last thing the entry holds is the title asked for.
        entries[0].set_text(title)
        button_labelled(dlg, "Add Event").clicked()
    return run_dialog(lambda: d.menu_action("File", "New Event"), [step])


def chip_named(d, prefix):
    for c in with_class(d.child, "eventhit", Gtk.Button):
        if any(isinstance(l, Gtk.Label) and l.get_text().startswith(prefix)
               for l in walk(c)):
            return c
    return None


def sidebar_cal_rows(d):
    return [l.get_text() for l in walk(d.app.cal_list_box)
            if isinstance(l, Gtk.Label)
            and "callabel" in l.get_style_context().list_classes()]


def main():
    toggles_part()
    findings_part()
    print("%d checks, %d passed, %d FAILED" % (COUNT, COUNT - len(FAILS), len(FAILS)))
    if FAILS:
        print("RESULT: FAILED")
        for f in FAILS:
            print("  " + f)
        return 1
    print("RESULT: ALL PASS")
    return 0


def toggles_part():
    errors = []
    real_hook = sys.excepthook

    def hook(et, ev, tb):
        errors.append(et.__name__)
        real_hook(et, ev, tb)
    sys.excepthook = hook

    d = appdrive.Drive("calendar")
    try:
        app = d.app
        toggles = cal_toggles(d)
        names = [n for n, v in app.cals_on.items() if isinstance(v, bool)]
        check("the sidebar has one real toggle per calendar",
              len(toggles) == len(names) and len(toggles) >= 1,
              "%d toggles, calendars %r" % (len(toggles), names))
        name, tog = names[0], toggles[0]

        # ---- click hides, click shows ---------------------------------------
        tog.clicked(); d.pump()
        check("clicking the row hides the calendar",
              app.cals_on[name] is False and tog.get_active() is False,
              "cals_on=%r active=%r" % (app.cals_on[name], tog.get_active()))
        tog.clicked(); d.pump()
        check("clicking it again shows it",
              app.cals_on[name] is True and tog.get_active() is True)

        # ---- an event filed into a HIDDEN calendar re-shows it -------------
        tog.clicked(); d.pump()                       # hidden again
        app.quick.set_text("Dentist 10:00")           # the quick-add entry
        app._on_quick_add(app.quick) if hasattr(app, "_on_quick_add") \
            else app.quick.emit("activate")
        d.pump(0.2)
        check("quick-adding an event into the hidden calendar shows it again",
              app.cals_on.get(name) is True, repr(app.cals_on.get(name)))
        check("...and the row toggle followed (mirrored, not stale)",
              tog.get_active() is True, repr(tog.get_active()))

        # ---- the click that used to recurse ------------------------------
        errors.clear()
        tog.clicked(); d.pump()
        check("clicking the row after a re-show raises nothing",
              not errors, "exceptions: %r" % (errors[:3],))
        check("...and hides the calendar once",
              app.cals_on[name] is False and tog.get_active() is False,
              "cals_on=%r active=%r" % (app.cals_on[name], tog.get_active()))

        # ---- the View menu path keeps the row in step too ------------------
        errors.clear()
        app._toggle_cal_by_name(name); d.pump()
        check("View > calendar shows it and lights the row",
              app.cals_on[name] is True and tog.get_active() is True and not errors,
              "cals_on=%r active=%r errors=%r" % (app.cals_on[name], tog.get_active(), errors[:2]))
        tog.clicked(); d.pump()
        check("...and the next row click still behaves (no recursion)",
              not errors and app.cals_on[name] is False,
              "errors=%r cals_on=%r" % (errors[:2], app.cals_on[name]))
    finally:
        sys.excepthook = real_hook
        d.close()
        shutil.rmtree(os.environ["NB_DRIVE_HOME_ROOT"], ignore_errors=True)


def findings_part():
    """The Aug-2026 real-use findings, at 1024x740, on a fresh store."""
    shutil.rmtree(os.environ["NB_DRIVE_HOME_ROOT"], ignore_errors=True)
    d = appdrive.Drive("calendar")
    try:
        app = d.app
        panel_h = d.h
        # A fixed anchor: the busiest-day and six-row-month cases below need
        # a known month. August 2026 (Saturday the 1st) has six rows; the
        # 22nd is a Saturday in row four.
        aug = date(2026, 8, 1)
        app.today = aug            # only steers the hint's "this year" test
        app.sel = date(2026, 8, 17)
        app.cur_y, app.cur_m = 2026, 8
        app.view = "month"
        app._refresh(); d.pump(0.2)

        # ---- F7: quick add understands a written year -----------------
        cal = d.mod
        got = cal.parse_quick_event("Old thing 3 january 2020 9am", app.sel)
        check("quick add files '3 january 2020' on 3 Jan 2020 with a clean name",
              got is not None and got[0] == "Old thing" and got[1] == date(2020, 1, 3),
              repr(got))
        got = cal.parse_quick_event("Reunion January 3, 2027 noon", app.sel)
        check("quick add reads 'January 3, 2027' the same way",
              got is not None and got[0] == "Reunion" and got[1] == date(2027, 1, 3)
              and got[2] == 12.0, repr(got))
        # (14 August is behind the selected 17th, so it resolves FORWARD to
        # next year — the documented rule; the point here is the "20".)
        got = cal.parse_quick_event("Party 14 august 20 guests", app.sel)
        check("a short number after the date stays in the name (not a year)",
              got is not None and got[0] == "Party 20 guests"
              and got[1] == date(2027, 8, 14), repr(got))
        got = cal.parse_quick_event("Leap 30 february 2020 9am", app.sel)
        check("an impossible year-date leaves the year in the name and keeps the pair",
              got is not None and "2020" in got[0], repr(got))
        hint = quick_add(d, "Old thing 3 january 2020 9am")
        check("the readback names the resolved year when it is not this year",
              "2020" in hint and app.title_lbl.get_text() == "January 2020",
              "hint=%r title=%r" % (hint, app.title_lbl.get_text()))
        check("...and the event landed on 3 Jan 2020 under its own name",
              any(e["title"] == "Old thing" and e["date"] == date(2020, 1, 3)
                  for e in app.events),
              repr([(e["title"], str(e["date"])) for e in app.events]))
        lay = app.title_lbl.get_layout()
        check("the month title 'January 2020' stays whole beside 'Added Old thing'",
              app.status_lbl.get_text().startswith("Added") and lay is not None
              and not lay.is_ellipsized(),
              "status=%r title=%r ellipsized=%s" % (
                  app.status_lbl.get_text(), app.title_lbl.get_text(),
                  lay is not None and lay.is_ellipsized()))

        # ---- F6: a status message never shortens the period title ------
        d.click("Today"); d.pump(0.1)
        app.sel = date(2026, 8, 17); app.cur_y, app.cur_m = 2026, 8
        app._refresh(); d.pump(0.1)
        quick_add(d, "Dentist 3pm"); d.pump(0.2)
        seg_x = None
        for view in ("Day", "Week", "Month"):
            d.click(view); d.pump(0.2)
            lay = app.title_lbl.get_layout()
            check("the %s title stays whole while a status message shows" % view,
                  app.status_lbl.get_text() != "" and lay is not None
                  and not lay.is_ellipsized(),
                  "status=%r title=%r ellipsized=%s alloc w=%d" % (
                      app.status_lbl.get_text(), app.title_lbl.get_text(),
                      lay is not None and lay.is_ellipsized(),
                      app.title_lbl.get_allocated_width()))
            st = app.status_lbl
            seg = app.seg_btns["day"].get_parent()
            seg_x = top_x(d, seg)
            text_right = top_x(d, st) + st.get_layout_offsets()[0] \
                - st.get_allocation().x + st.get_layout().get_pixel_size()[0]
            if view == "Week":
                # 1024 wide, the week title fills the row: the room left for
                # the message would hold no more than its ellipsis
                check("a message with room only for its ellipsis is faded, not shown as '…'",
                      st.get_text() != "" and st.get_opacity() == 0.0,
                      "status=%r opacity=%s alloc w=%d" % (
                          st.get_text(), st.get_opacity(), st.get_allocated_width()))
            else:
                check("the %s view's message is shown, clear of the view segment" % view,
                      st.get_opacity() == 1.0 and seg_x is not None
                      and text_right <= seg_x - 8,
                      "opacity=%s text right edge %s, segment x %s" % (
                          st.get_opacity(), text_right, seg_x))
        d.pump(4.5)                                    # let the status clear
        # The same row in Greek, whose month title fills the header exactly
        # at 1024 wide (see _build_main): a message must not cost it a pixel.
        # A fresh process, since nbi18n reads $NB_LANG once at import.
        code = (
            "import os, sys; sys.path.insert(0, %r); import appdrive\n"
            "d = appdrive.Drive('calendar'); a = d.app\n"
            "a.view = 'month'; a._refresh(); d.pump(0.2)\n"
            "before = a.title_lbl.get_layout().is_ellipsized()\n"
            "a._flash_status('Added Dentist'); d.pump(0.3)\n"
            "print('EL', before, a.title_lbl.get_layout().is_ellipsized(), "
            "a.title_lbl.get_text(), '|', a.status_lbl.get_text())\n"
            "d.close()\n" % HERE)
        env = dict(os.environ, NB_LANG="el",
                   NB_DRIVE_HOME_ROOT=os.path.join(os.environ["NB_DRIVE_HOME_ROOT"], "el"))
        try:
            out = subprocess.run([sys.executable, "-c", code], env=env,
                                 capture_output=True, text=True, timeout=120).stdout
        except Exception as exc:                                  # noqa: BLE001
            out = "EL run failed %r" % (exc,)
        line = [l for l in out.splitlines() if l.startswith("EL ")]
        check("the Greek month title, whole without a message, stays whole with one",
              bool(line) and line[0].split()[1:3] == ["False", "False"],
              repr(line or out[-300:]))

        # ---- F3: an all-day event shows in the Week view -----------------
        d.click("Month"); d.pump(0.1)
        new_event(d, "Grandma's birthday party", all_day=True)
        d.click("Week"); d.pump(0.2)
        bands = with_class(d.child, "alldayband")
        week_chips = [l.get_text() for c in with_class(d.child, "eventhit", Gtk.Button)
                      for l in walk(c) if isinstance(l, Gtk.Label)]
        check("the Week view has an all-day band when the week holds an all-day event",
              len(bands) >= 1, "alldayband widgets: %d" % len(bands))
        check("...and the all-day event's chip is in it",
              any(t.startswith("Grandma") for t in week_chips), repr(week_chips))
        d.click("Day"); d.pump(0.2)
        check("the Day view still shows the same band",
              len(with_class(d.child, "alldayband")) == 1)

        # ---- F5: three events on one day of a six-row month fit the panel --
        d.click("Month"); d.pump(0.2)
        quick_add(d, "Alpha 22 august 9am")
        quick_add(d, "Beta 22 august 11am")
        quick_add(d, "Gamma 22 august 1pm")
        d.pump(0.5)

        def month_fits(tag):
            grid = app.month_grid
            gy = top_y(d, grid)
            gb = gy + grid.get_allocated_height() if gy is not None else None
            btn = None
            for b in d.find(Gtk.Button):
                if any(isinstance(l, Gtk.Label) and l.get_text().startswith("New Event")
                       for l in walk(b)):
                    btn = b
            by = top_y(d, btn) if btn else None
            bb = by + btn.get_allocated_height() if by is not None else None
            check("%s: the month grid ends inside the panel" % tag,
                  gb is not None and gb <= panel_h,
                  "grid bottom %s, panel %d" % (gb, panel_h))
            check("%s: the sidebar's New Event button is fully on screen" % tag,
                  bb is not None and bb <= panel_h,
                  "button bottom %s, panel %d" % (bb, panel_h))
        month_fits("three events on the 22nd")
        more = [l.get_text() for l in walk(d.child) if isinstance(l, Gtk.Label)
                and "evmore" in l.get_style_context().list_classes()]
        chips22 = [l.get_text() for l in walk(d.child) if isinstance(l, Gtk.Label)
                   and "evchip" in l.get_style_context().list_classes()
                   and l.get_text() in ("Alpha", "Beta", "Gamma")]
        check("...and every event of that day is reachable: shown as a chip or counted in '+N more'",
              len(chips22) == 3 or (more and len(chips22) + int(more[0].split()[0].lstrip("+")) == 3),
              "chips %r more %r" % (chips22, more))
        quick_add(d, "Delta 22 august 3pm"); d.pump(0.5)
        month_fits("four events on the 22nd")

        # ---- F4: Add a Shift puts Work in the sidebar at once -----------
        def shift_step(dlg):
            ents = [w for w in walk(dlg) if isinstance(w, Gtk.Entry)]
            ents[0].set_text("Late shift")
            ents[1].set_text("22:00")
            ents[2].set_text("06:00")
            button_labelled(dlg, "Add Shift").clicked()
        run_dialog(lambda: d.menu_action("File", "Add a Shift"), [shift_step])
        d.pump(0.3)
        check("Add a Shift creates the Work calendar",
              any(c["name"] == cal.WORK_CAL for c in app.calendars),
              repr([c["name"] for c in app.calendars]))
        check("...and the sidebar lists it the moment it exists",
              cal.WORK_CAL in sidebar_cal_rows(d), repr(sidebar_cal_rows(d)))
        check("...with a real toggle row for it",
              len(cal_toggles(d)) == len(app.calendars),
              "%d toggles for %d calendars" % (len(cal_toggles(d)), len(app.calendars)))

        # ---- F8 / F10: the New Event dialog ------------------------------
        seen = {}

        def dlg_step(dlg):
            combos = [w for w in walk(dlg) if isinstance(w, Gtk.ComboBoxText)]
            fields = combos[0].get_parent().get_parent()
            # The COMBOS themselves, each on its own, not the whole field box:
            # the box also holds the STARTS / DURATION captions, and one of
            # them changing was enough to pass a whole-box comparison while
            # the controls a person actually reads stayed at full ink.
            caps = [l for l in with_class(fields, "dlgfield", Gtk.Label)]
            before = [dialog_pixels(dlg, c) for c in combos[:2]]
            before_caps = [dialog_pixels(dlg, l) for l in caps[:2]]
            for c in walk(dlg):
                if isinstance(c, Gtk.CheckButton) and c.get_label() == "All Day":
                    c.set_active(True)
            appdrive.pump(0.2)
            after = [dialog_pixels(dlg, c) for c in combos[:2]]
            after_caps = [dialog_pixels(dlg, l) for l in caps[:2]]
            seen["insensitive"] = not fields.get_sensitive()
            seen["differs"] = (len(before) == 2 and all(len(b) > 0 for b in before)
                               and all(b != a for b, a in zip(before, after)))
            seen["caps_differ"] = (len(before_caps) == 2
                                   and all(len(b) > 0 for b in before_caps)
                                   and all(b != a for b, a
                                           in zip(before_caps, after_caps)))
            for c in walk(dlg):
                if isinstance(c, Gtk.CheckButton) and c.get_label() == "All Day":
                    c.set_active(False)
            # F10: a Notes frame, and a bad end date that says what a date looks like
            tv = [w for w in walk(dlg) if isinstance(w, Gtk.TextView)][0]
            anc, framed = tv.get_parent(), False
            while anc is not None and anc is not dlg:
                if "notesframe" in anc.get_style_context().list_classes():
                    framed = True
                anc = anc.get_parent()
            seen["framed"] = framed
            ents = [w for w in walk(dlg) if isinstance(w, Gtk.Entry)]
            ents[0].set_text("Yoga")
            end = [e for e in ents
                   if "end date" in (e.get_placeholder_text() or "").lower()][0]
            end.set_text("yesterday")
            seen["end"] = end
            button_labelled(dlg, "Add Event").clicked()

        def dlg_step2(dlg):
            seen["stayed"] = dlg.get_visible()
            errs = [l.get_text() for l in with_class(dlg, "dlgerror", Gtk.Label)
                    if l.get_visible() and l.get_mapped()]
            seen["errors"] = errs
            seen["end"].set_text("")
            appdrive.pump(0.1)
            seen["cleared"] = [l for l in with_class(dlg, "dlgerror", Gtk.Label)
                               if l.get_visible()] == []
            dlg.response(Gtk.ResponseType.CANCEL)
        ran = run_dialog(lambda: d.menu_action("File", "New Event"),
                         [dlg_step, dlg_step2])
        check("the New Event dialog opened for the dialog checks", ran == 2,
              "%d dialog steps ran" % ran)
        check("All Day makes the Starts / Duration fields insensitive",
              seen.get("insensitive") is True)
        check("...and the Starts / Duration combos LOOK disabled (greyed, not identical pixels)",
              seen.get("differs") is True)
        check("...and their captions grey with them",
              seen.get("caps_differ") is True)
        check("Notes sits in a framed field like the entries above it",
              seen.get("framed") is True)
        check("a bad Series End Date keeps the dialog open",
              seen.get("stayed") is True)
        check("...and says in words what an end date looks like",
              any(e and e != "Enter an event title." for e in seen.get("errors", [])),
              repr(seen.get("errors")))
        check("...and the message clears when the field changes",
              seen.get("cleared") is True)

        # ---- F9: Delete Repeating Event names the event and its count -----
        new_event(d, "Yoga", repeat_idx=1)                     # every day
        yoga = [e for e in app.events if e["title"] == "Yoga"]
        check("a daily event was created for the delete check", len(yoga) > 1,
              "%d Yoga events" % len(yoga))
        scope = {}

        def scope_step(dlg):
            scope["labels"] = labels_in(dlg)
            scope["buttons"] = {}
            for b in walk(dlg):
                if isinstance(b, Gtk.Button):
                    txt = [l.get_text() for l in walk(b) if isinstance(l, Gtk.Label)]
                    if txt:
                        scope["buttons"][txt[0]] = sorted(
                            b.get_style_context().list_classes())
            dlg.response(Gtk.ResponseType.CANCEL)
        run_dialog(lambda: app._delete_event(yoga[0]), [scope_step])
        labels = scope.get("labels", [])
        check("the delete-scope dialog names the event and how often it repeats",
              any("Yoga" in t and str(len(yoga)) in t for t in labels), repr(labels))
        btns = scope.get("buttons", {})
        check("...and its three deleting choices read as destructive",
              all("destructive" in btns.get(k, []) for k in
                  ("This Occurrence Only", "This and Following", "Whole Series")),
              repr(btns))
        check("...while Cancel does not",
              "destructive" not in btns.get("Cancel", ["destructive"]), repr(btns.get("Cancel")))
        check("cancelling the scope dialog deletes nothing",
              len([e for e in app.events if e["title"] == "Yoga"]) == len(yoga))
    finally:
        d.close()
        shutil.rmtree(os.environ["NB_DRIVE_HOME_ROOT"], ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
