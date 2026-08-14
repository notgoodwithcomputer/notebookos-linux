#!/usr/bin/env python3
"""notify_selftest — the notification centre, store and surface.

    DISPLAY=:0 tools/guestrun.sh python3 tools/notify_selftest.py

The notification centre is two halves that fail in completely different ways,
so this proves both against the real code rather than a description of it:

1. **The spool** (`de/nbnotify.py`) is written by many app processes at once and
   read by the panel. What can go wrong here is not cosmetic: a record lost to a
   concurrent write, a damaged file emptying the whole tray, an id that escapes
   the spool directory and unlinks something else, or an expiry that DELETES on
   the read path — the shape of the worst defect this OS has ever produced.
2. **The surface** (`de/shell.py`) is the most-seen chrome in the system. What
   is checked is what a person would notice: that the mark sits where it was
   put and never moves, that the tray rests on the line the rest of the cluster
   ends on, that a message names its sender and its time, that the empty state
   is the same card as the full one, and that dismissing one message leaves the
   others alone.

Every family ends with a MUTANT: the same check re-run against a deliberately
broken version, which must go RED. A gate that has never failed has not been
tested, it has been observed.

Exit status is the number of failures.
"""
import os
import shutil
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO,
                  "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, DE)
_HOME = tempfile.mkdtemp(prefix="notify-selftest-")
os.environ["NB_HOME"] = _HOME

import nbnotify                                                  # noqa: E402

FAILS = []
CHECKS = [0]


def check(name, ok, detail=""):
    CHECKS[0] += 1
    print("%-4s %s%s" % ("ok" if ok else "FAIL", name,
                         "" if ok else "   -> " + str(detail)))
    if not ok:
        FAILS.append(name)


def mutant(name, ok_when_broken):
    CHECKS[0] += 1
    caught = not ok_when_broken
    print("%-4s MUTANT %s%s" % ("ok" if caught else "FAIL", name,
                                "" if caught else
                                "   -> sabotage went UNDETECTED"))
    if not caught:
        FAILS.append("MUTANT " + name)


def wipe():
    nbnotify.clear_all()
    try:
        os.remove(nbnotify.SEEN_FILE)
    except OSError:
        pass


# ===================================================== 1. the spool contract
print("--- 1. posting and reading ---------------------------------------")
wipe()

nbnotify.post("Finished writing the stick", "notebookos-1.2.iso",
              app="usbwriter", app_name="USB Writer")
nbnotify.post("The disc is written", "It can be taken out.",
              app="burner", app_name="Disc Burner")
items = nbnotify.load()
check("both messages come back", len(items) == 2, len(items))
check("newest first", items[0]["title"] == "The disc is written",
      items[0]["title"])
check("the sending app is recorded", items[0]["app"] == "burner")
check("...and its display name", items[0]["app_name"] == "Disc Burner")
check("every record carries an id", all(r.get("id") for r in items))
check("the body survives the round trip",
      items[1]["body"] == "notebookos-1.2.iso")
check("a post is one file, not a shared store",
      len([n for n in os.listdir(nbnotify.SPOOL) if n.endswith(".json")]) == 2)

# Two senders in the same microsecond: the exact case a single JSON store
# loses. The file NAME must differ even when the timestamp does not.
at = time.time()
names = {nbnotify._stamp_name(at, i) for i in range(50)}
check("50 posts at one timestamp make 50 distinct file names", len(names) == 50)
mutant("distinct names per post",
       len({nbnotify._stamp_name(at, 0) for _ in range(50)}) == 50)

# Chronological order must come from the NAME alone: prune() sorts without
# opening a single record, so an unparseable file still expires on schedule.
early, late = nbnotify._stamp_name(1000.0, 1), nbnotify._stamp_name(2000.0, 1)
check("file names sort chronologically as plain text", early < late,
      (early, late))
mutant("name ordering is not accidental",
       ("%d-1.json" % 1000) > ("%d-1.json" % 2000))

print("--- 2. a damaged record cannot empty the tray --------------------")
with open(os.path.join(nbnotify.SPOOL, "0000000000000009-1-1.json"), "w") as fh:
    fh.write("{ this is not json")
with open(os.path.join(nbnotify.SPOOL, "0000000000000010-1-1.json"), "w") as fh:
    fh.write('{"at": "not a number", "title": "x"}')
with open(os.path.join(nbnotify.SPOOL, "0000000000000011-1-1.json"), "w") as fh:
    fh.write('["a list, not a record"]')
survivors = nbnotify.load()
check("three unreadable records are skipped, the good ones stay",
      len(survivors) == 2, len(survivors))
check("...and NOTHING was deleted to achieve that",
      len([n for n in os.listdir(nbnotify.SPOOL) if n.endswith(".json")]) == 5)
mutant("loading is not all-or-nothing",
       len(survivors) == 0)

# A temp file caught mid-rename is not a notification.
with open(os.path.join(nbnotify.SPOOL, ".nbw-abcd.tmp"), "w") as fh:
    fh.write('{"at": 1, "title": "half a write"}')
check("an in-flight temp file is not read as a message",
      len(nbnotify.load()) == 2)
mutant("temp files are actually excluded",
       nbnotify._is_record(".nbw-abcd.tmp"))

print("--- 3. expiry filters on read, and only deletes on write ---------")
wipe()
old = time.time() - nbnotify.MAX_AGE_S - 3600
import nbapp                                                     # noqa: E402
stale = nbnotify._stamp_name(old, 1)
nbapp.atomic_write_json(os.path.join(nbnotify.SPOOL, stale),
                        {"at": old, "title": "last week", "app": "",
                         "app_name": "", "icon": "", "body": ""})
check("an expired message is not shown", nbnotify.load() == [])
check("...and reading did NOT unlink it",
      os.path.exists(os.path.join(nbnotify.SPOOL, stale)))
nbnotify.post("something new", app="burner", app_name="Disc Burner")
check("the next post sweeps it",
      not os.path.exists(os.path.join(nbnotify.SPOOL, stale)))
mutant("expiry really is enforced",
       nbnotify.MAX_AGE_S <= 0)

print("--- 4. the tray is bounded ---------------------------------------")
wipe()
for n in range(nbnotify.MAX_KEEP + 12):
    nbnotify.post("message %d" % n, app="burner", app_name="Disc Burner")
kept = [n for n in os.listdir(nbnotify.SPOOL) if n.endswith(".json")]
check("the spool is capped at MAX_KEEP", len(kept) == nbnotify.MAX_KEEP,
      len(kept))
check("the ones kept are the NEWEST",
      nbnotify.load()[0]["title"] == "message %d" % (nbnotify.MAX_KEEP + 11))
mutant("the cap is doing the capping",
       len(kept) == nbnotify.MAX_KEEP + 12)

wipe()
nid = nbnotify.post("x" * 4000, "y" * 9000, app="burner", app_name="Disc Burner")
rec = nbnotify.load()[0]
check("a runaway title is capped at post time",
      len(rec["title"]) == nbnotify.MAX_TITLE, len(rec["title"]))
check("...and so is a runaway body",
      len(rec["body"]) == nbnotify.MAX_BODY, len(rec["body"]))
mutant("the caps are applied", len(rec["title"]) == 4000)

print("--- 5. dismissal cannot reach outside the spool -------------------")
wipe()
# The victim is placed where a traversing id would ACTUALLY land: one level up
# from the spool, under the name the code would build (`<id>.json`). An earlier
# draft of this test put it in $NB_HOME under a name with no .json suffix — so
# every evil id "failed" simply because nothing was there to delete, and the
# test passed with the guard removed. A refusal that cannot be told apart from
# a miss is not evidence of a refusal.
victim = os.path.join(nbnotify.CFG_DIR, "DO-NOT-DELETE.json")
os.makedirs(nbnotify.CFG_DIR, exist_ok=True)
with open(victim, "w") as fh:
    fh.write('{"not": "a notification"}')
a = nbnotify.post("first", app="burner", app_name="Disc Burner")
b = nbnotify.post("second", app="video", app_name="Video Editor")
# An ABSOLUTE id is the one that really reaches: os.path.join throws away
# everything to its left when the right-hand side is absolute, so `SPOOL` never
# appears in the resulting path at all. A relative "../x" is the obvious attempt
# and the harmless one — it only lands if the directory it walks through
# happens to exist — which is why it cannot be the only case tested.
absolute = victim[:-len(".json")]
for evil in (absolute, "/etc/passwd", "a/../../DO-NOT-DELETE",
             "../DO-NOT-DELETE", "sub/dir", "..", ".", "", None, 17):
    check("dismiss refuses %r" % (evil,), nbnotify.dismiss(evil) is False)
check("...and the file one level up is still there", os.path.exists(victim))
check("...and the two real messages are untouched", len(nbnotify.load()) == 2)
mutant("the id check is what refuses it",
       nbnotify._record_path(absolute) is not None)

check("dismissing one removes exactly one", nbnotify.dismiss(b))
left = nbnotify.load()
check("...and leaves the other alone",
      len(left) == 1 and left[0]["title"] == "first", left)
check("clear_all reports what it removed", nbnotify.clear_all() == 1)
check("...and the tray is then empty", nbnotify.load() == [])

print("--- 6. unread is counted against the last look --------------------")
wipe()
check("an empty tray has nothing unread", nbnotify.unread_count() == 0)
nbnotify.post("one", app="burner", app_name="Disc Burner")
nbnotify.post("two", app="burner", app_name="Disc Burner")
check("two arrivals, two unread", nbnotify.unread_count() == 2)
opened = time.time()
nbnotify.mark_seen(opened)
check("opening the tray clears the count", nbnotify.unread_count() == 0)
time.sleep(0.01)
nbnotify.post("three", app="burner", app_name="Disc Burner")
check("a message that lands AFTER the look is unread again",
      nbnotify.unread_count() == 1, nbnotify.unread_count())
check("...and the older two stay read", len(nbnotify.load()) == 3)
mutant("the read mark is what silences them",
       nbnotify.unread_count(nbnotify.load()) == 3)

print("--- 7. the poll key changes only when the tray does ---------------")
wipe()
nbnotify.post("one", app="burner", app_name="Disc Burner")
k1 = nbnotify.state_key()
check("an unchanged tray reports an unchanged key",
      nbnotify.state_key() == k1)
nbnotify.post("two", app="burner", app_name="Disc Burner")
check("a new message moves the key", nbnotify.state_key() != k1)
k2 = nbnotify.state_key()
nbnotify.dismiss(nbnotify.load()[0]["id"])
check("a dismissal moves it too", nbnotify.state_key() != k2)
k3 = nbnotify.state_key()
nbnotify.mark_seen()
check("so does opening the tray", nbnotify.state_key() != k3)
check("the key never opens a record",
      "at" not in str(nbnotify.state_key()))
mutant("the key is not simply constant",
       nbnotify.state_key() == k1)

# ======================================================== 8. the surface
print("--- 8. the panel ---------------------------------------------------")
try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    import shell
    HAVE_GTK = Gtk.init_check(None)[0]
except Exception as exc:                                          # noqa: BLE001
    HAVE_GTK, shell, Gtk = False, None, None
    print("     (no display: %s)" % exc)

if not HAVE_GTK:
    # Never report a display-dependent claim as verified without a display.
    for name in ("the mark is in the bar", "the tray rests on the margin",
                 "rows are built newest first", "the empty state is a card",
                 "dismiss leaves the others"):
        check(name + "  [NOT REACHED: no display]", False)
else:
    def pump(n=30):
        for _ in range(n):
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)

    wipe()
    panel = shell.Panel()
    panel.show_all()
    pump()

    cluster = panel.clocklbl.get_parent().get_children()
    check("the mark is in the right cluster", panel.bell in cluster)
    # MEASURED, not read off the child list. Gtk.Box returns its children in
    # packing order, which is not the order they are drawn in, and a mutation
    # that moves the mark can leave that list untouched: pack_start and
    # pack_end put a widget in the same place in a box that is itself packed
    # against the right edge. Where a thing IS on the bar is its allocation.
    def at(w):
        a = w.get_allocation()
        return (a.x, a.x + a.width)

    bell_l, bell_r = at(panel.bell)
    clock_l, _clock_r = at(panel.clocklbl)
    check("...to the LEFT of the clock", bell_r <= clock_l,
          (at(panel.bell), at(panel.clocklbl)))
    check("...and nothing sits between them",
          not any(at(c)[0] >= bell_r and at(c)[1] <= clock_l
                  for c in cluster if c is not panel.bell
                  and c is not panel.clocklbl and c.get_visible()))
    check("it is a menu title, styled like File and View",
          "menuitem" in panel.bell.get_style_context().list_classes())

    # The mark must never change width, or the cluster beside it moves.
    quiet = shell.bell_surface(False)
    loud = shell.bell_surface(True)
    check("the two states of the mark are the same size",
          quiet is not None and loud is not None
          and (quiet.get_width(), quiet.get_height())
          == (loud.get_width(), loud.get_height()))
    check("...and the unread one really is a different drawing",
          quiet.get_data() != loud.get_data())
    mutant("the sizes are compared, not assumed",
           (quiet.get_width(), quiet.get_height()) == (loud.get_width() + 1,
                                                       loud.get_height()))

    check("a quiet tray says so", panel._bell_unread is False)
    for mod, name, title in (("usbwriter", "USB Writer", "one"),
                             ("video", "Video Editor", "two"),
                             ("burner", "Disc Burner", "three")):
        nbnotify.post(title, "body of " + title, app=mod, app_name=name)
    panel._tick()
    check("the mark lights when messages arrive",
          panel._bell_unread is True)
    check("...and the exact count is in its accessible name",
          "3" in (panel.bell.get_tooltip_text() or ""),
          panel.bell.get_tooltip_text())

    panel._notify_menu(panel.bell)
    pump()
    check("the tray opens", panel._menu is not None)
    x, y, w, h = panel._menu_rect
    check("it hangs from the bar", y == shell.PANEL_H, y)
    check("its right edge rests on the cluster's margin",
          x + w == panel.screen_w - shell.RIGHT_MARGIN,
          (x + w, panel.screen_w - shell.RIGHT_MARGIN))
    check("it fits the smallest supported screen", w <= 1024 - 2 * 20, w)
    check("opening it clears the mark", panel._bell_unread is False)

    def rows_of(p):
        return [c for c in p._menu.get_child().get_children()
                if "nbn-row" in c.get_style_context().list_classes()]

    rows = rows_of(panel)
    check("every message has a row", len(rows) == 3, len(rows))
    check("newest first, here too",
          "Disc Burner" in (rows[0].get_tooltip_text() or ""),
          rows[0].get_tooltip_text())
    check("a row says what clicking it does",
          all("Open" in (r.get_tooltip_text() or "") for r in rows))
    full_w = panel._menu_rect[2]

    # The cross inside a row dismisses that row and nothing else.
    cross = [c for c in rows[1].get_child().get_children()
             if isinstance(c, Gtk.Button)][0]
    cross.emit("clicked")
    pump()
    check("the cross removes one message", len(nbnotify.load()) == 2)
    check("...the tray stays open on the rest", len(rows_of(panel)) == 2)
    check("...and does not move", panel._menu_rect[2] == full_w)
    mutant("dismissal is what removed it", len(nbnotify.load()) == 3)

    head = [c for c in panel._menu.get_child().get_children()
            if "nbn-head" in c.get_style_context().list_classes()][0]
    head.get_children()[-1].emit("clicked")
    pump()
    check("Clear All empties the tray", nbnotify.load() == [])
    check("...and the empty state appears in place",
          any("nbn-empty" in c.get_style_context().list_classes()
              for c in panel._menu.get_child().get_children()))
    check("the empty card is the same width as the full one",
          panel._menu_rect[2] == full_w, (panel._menu_rect[2], full_w))
    check("an empty tray offers no Clear All",
          len([c for c in panel._menu.get_child().get_children()
               if "nbn-head" in c.get_style_context().list_classes()][0]
              .get_children()) == 1)

    # A surface you READ must not time out like a list you pick from.
    check("the tray gets the long idle span",
          panel._menu_idle_s == shell.NOTIFY_IDLE_TIMEOUT_S,
          panel._menu_idle_s)
    panel._notify_menu(panel.bell)
    pump()
    check("a second click on the mark shuts it", panel._menu is None)
    def titles(widget, out=None):
        out = [] if out is None else out
        if (isinstance(widget, Gtk.Button)
                and "menuitem" in widget.get_style_context().list_classes()
                and widget is not panel.bell):
            out.append(widget)
        if isinstance(widget, Gtk.Container):
            for kid in widget.get_children():
                titles(kid, out)
        return out

    panel._view_menu(titles(panel.fixed)[0])
    pump()
    check("...while an ordinary menu keeps the short one",
          panel._menu_idle_s == shell.MENU_IDLE_TIMEOUT_S,
          panel._menu_idle_s)
    mutant("the two spans really differ",
           shell.NOTIFY_IDLE_TIMEOUT_S == shell.MENU_IDLE_TIMEOUT_S)
    panel._menu_close()

    # Relative time, at every resolution it claims to have.
    now = time.time()
    check("a message from seconds ago says just now",
          panel._notify_when(now - 5) == shell._t("Just now"))
    check("minutes are counted", "3" in panel._notify_when(now - 200),
          panel._notify_when(now - 200))
    check("hours ago today falls back to the clock",
          ":" in panel._notify_when(now - 4 * 3600)
          or panel._notify_when(now - 4 * 3600) == shell._t("Yesterday"))
    # Thirty hours ago is not "yesterday" — before 06:00 it lands on the day
    # BEFORE yesterday, so this check passed all afternoon and failed every
    # early morning, naming the app for the clock the runner happened to
    # start at. The same time of day one day back is yesterday at any hour.
    y_at = now - 24 * 3600
    check("the fixture really is one calendar day back",
          shell.Panel._days_between(time.localtime(y_at),
                                    time.localtime(now)) == 1,
          (time.strftime("%d %b %H:%M", time.localtime(y_at)),
           time.strftime("%d %b %H:%M", time.localtime(now))))
    check("yesterday is named",
          panel._notify_when(y_at) == shell._t("Yesterday"),
          panel._notify_when(y_at))
    check("older than that becomes a date",
          panel._notify_when(now - 6 * 86400) not in
          (shell._t("Yesterday"), shell._t("Just now")))
    check("a message stamped in the future is not 'in -3 hours'",
          panel._notify_when(now + 3 * 3600) == shell._t("Just now"))
    mutant("the day arithmetic is real",
           shell.Panel._days_between(time.localtime(now - 86400),
                                     time.localtime(now)) == 0)

    # The tick must not touch a widget on a second when nothing happened.
    wipe()
    panel._tick()
    before = panel._notify_state
    painted = []
    real_paint = panel._paint_bell
    panel._paint_bell = lambda n: painted.append(n)
    for _ in range(5):
        panel._tick()
    panel._paint_bell = real_paint
    check("five quiet ticks repaint the mark zero times",
          painted == [], painted)
    check("...and the poll key stayed put", panel._notify_state == before)
    nbnotify.post("wake up", app="burner", app_name="Disc Burner")
    panel._tick()
    check("a real arrival does reach the mark", panel._bell_unread is True)

print("-" * 68)
print("%d checks, %d failed" % (CHECKS[0], len(FAILS)))
for f in FAILS:
    print("   FAILED: " + f)
shutil.rmtree(_HOME, ignore_errors=True)
sys.exit(len(FAILS))
