#!/usr/bin/env python3
"""burner_realuse_selftest — drive the Disc Burner the way a person does.

    tools/guestrun.sh python3 tools/burner_realuse_selftest.py

burner_selftest proves the arithmetic, the menu geometry and the commands
against the real functions. It cannot see the app: every defect this file
exists for was invisible to it because the app was never RUN.

So this one runs it. A real DiscBurner window is hosted in an offscreen holder
at the smallest supported panel (1024x740, tools/appdrive.py), with fake burn
tools on PATH, a fake drive and a fake disc, and then driven through what a
person does: add songs, press Burn disc, switch mode, open the File menu,
select a row, put in the wrong disc, close during a write. Nothing here touches
a real drive; what it looks at is what the app puts on screen and in the tray.

Every check is named and fails BY NAME — a check that crashes is reported as a
failure of that check, never as a broken run. Exit status is the failure count.
"""
import io
import json
import glob
import os
import stat
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO,
                  "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, DE)

WORK = tempfile.mkdtemp(prefix="burner-realuse-")
BIN = os.path.join(WORK, "bin")
MEDIA = os.path.join(WORK, "media")
MARK = os.path.join(WORK, "marks")
for d in (BIN, MEDIA, MARK):
    os.makedirs(d, exist_ok=True)
os.environ["NB_DRIVE_HOME_ROOT"] = os.path.join(WORK, "home")
os.environ["NB_FAKE_MARK"] = MARK
os.environ["PATH"] = BIN + os.pathsep + os.environ.get("PATH", "")
os.environ.setdefault("DISPLAY", ":0")


# ---- the tools the app shells out to, faked -------------------------------
# Fast, chatty in the shapes the app parses, and each one leaves a mark so a
# check can tell "the burn reported success" from "the burn actually reached
# the drive". Every writer sleeps once for longer than _step's 0.4s select
# timeout, so BOTH of its cancellation checks are exercised by every burn.
FAKES = {
    "ffmpeg": """#!/bin/sh
out=""
for a in "$@"; do out="$a"; done
echo "ffmpeg version fake"
case " $* " in *BADFILE*) echo "Invalid data found" >&2; exit 1;; esac
echo "out_time_ms=1000000"
echo "out_time_ms=3000000"
mkdir -p "$(dirname "$out")" 2>/dev/null
head -c 4096 /dev/zero > "$out"
echo "progress=end"
exit 0
""",
    "ffprobe": """#!/bin/sh
f=""
for a in "$@"; do f="$a"; done
case "$f" in *UNREADABLE*) exit 1;; esac
echo 5.0
""",
    "wodim": """#!/bin/sh
i=0
while [ $i -lt 4 ]; do
  echo "Track 01: writing"
  sleep 0.6
  i=$((i+1))
done
echo "Fixating..."
touch "$NB_FAKE_MARK/wodim.ran"
exit 0
""",
    "growisofs": """#!/bin/sh
echo "  0% done"
sleep 0.6
echo "100% done"
touch "$NB_FAKE_MARK/growisofs.ran"
exit 0
""",
    "genisoimage": """#!/bin/sh
out=""; prev=""
for a in "$@"; do
  if [ "$prev" = "-o" ]; then out="$a"; fi
  prev="$a"
done
[ -n "$out" ] && head -c 4096 /dev/zero > "$out"
exit 0
""",
    "dvdauthor": """#!/bin/sh
echo "INFO: dvdauthor creating table of contents"
exit 0
""",
    "spumux": """#!/bin/sh
cat
exit 0
""",
    "dvd+rw-mediainfo": """#!/bin/sh
echo "INQUIRY: fake"
exit 0
""",
}
for name, body in FAKES.items():
    path = os.path.join(BIN, name)
    io.open(path, "w", encoding="utf-8").write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP)

SONG = os.path.join(MEDIA, "Song One.wav")
SONG2 = os.path.join(MEDIA, "Second song.wav")
SONG3 = os.path.join(MEDIA, "Third song.wav")
UNREADABLE = os.path.join(MEDIA, "UNREADABLE song.wav")
BADFILE = os.path.join(MEDIA, "BADFILE song.wav")
CLIP = os.path.join(MEDIA, "Holiday clip.mp4")
CLIP2 = os.path.join(MEDIA, "Garden clip.mp4")
for path in (SONG, SONG2, SONG3, UNREADABLE, BADFILE, CLIP, CLIP2):
    io.open(path, "wb").write(b"\0" * 2048)

import appdrive                                              # noqa: E402
from gi.repository import Gdk, Gtk                           # noqa: E402
import nbjobs                                                # noqa: E402
import nbpicker                                              # noqa: E402
import burner                                                # noqa: E402

FAILS = []
CHECKS = [0]


def shield_from_the_display():
    """Ignore keystrokes that did not come from this drive.

    nbapp.track_input_modality() installs a PROCESS-WIDE GDK dispatcher, so
    every key pressed anywhere on the shared X display arrives here as a real
    event and lands on the window under test. One stray Escape between two
    checks closes it: the burn in flight reports "Stopping…", and every file
    added to the next window is dropped in silence because its JobOwner is
    already closed. That made this file fail in a different place each run,
    for a reason that had nothing to do with the app.

    The drive delivers its own keys through widget.emit() (appdrive._deliver_
    key), never through this path, so dropping real key events costs the suite
    nothing — including section 8, which drives Escape mid-burn on purpose.
    Everything else is handed on to GTK exactly as before.
    """
    def dispatch(event, *_a):
        if event.type in (Gdk.EventType.KEY_PRESS, Gdk.EventType.KEY_RELEASE):
            return
        Gtk.main_do_event(event)
    try:
        Gdk.event_handler_set(dispatch)
    except Exception:                                         # noqa: BLE001
        pass                       # unshielded is how it ran before


def check(name, ok, detail=""):
    CHECKS[0] += 1
    print("%-4s %s%s" % ("ok" if ok else "FAIL", name,
                         "" if ok else "   -> " + str(detail)), flush=True)
    if not ok:
        FAILS.append(name)


def section(title):
    print("\n--- %s %s" % (title, "-" * max(0, 62 - len(title))), flush=True)


def run(fn):
    """Run one section; anything it raises fails the checks it did not reach."""
    try:
        fn()
    except Exception as exc:                                  # noqa: BLE001
        import traceback
        traceback.print_exc()
        check("%s finished without raising" % fn.__name__, False,
              "%s: %s" % (type(exc).__name__, exc))


# ---- dialogs: read what the card says, answer it as a person would ---------
DIALOGS = []
ANSWER = [Gtk.ResponseType.CANCEL]


def _card_text(widget, out):
    if isinstance(widget, Gtk.Button):
        out.append("[%s]" % widget.get_label())
    elif isinstance(widget, Gtk.Label):
        if widget.get_text():
            out.append(widget.get_text())
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            _card_text(child, out)


def _fake_run(self):
    out = []
    _card_text(self, out)
    DIALOGS.append(out)
    return ANSWER[0]


Gtk.Dialog.run = _fake_run


# ---- driving ---------------------------------------------------------------
BLANK_CD = {"media": "CD-R", "blank": True, "present": True,
            "bytes": 700 * 1000 * 1000}
BLANK_DVD = {"media": "DVD+R", "blank": True, "present": True,
             "bytes": 4700372992}


def make(disc=None, size=(1024, 740)):
    d = appdrive.Drive("burner", size=size)
    # Building a window installs the app chrome's CSS, which re-installs
    # nbapp's own dispatcher, so the shield goes back on after every window.
    shield_from_the_display()
    mod = d.mod
    mod.optical_drives = lambda: [{"name": "sr0", "node": "/dev/sr0",
                                   "label": "HL-DT-ST DVDRAM GH24"}]
    info = dict(disc or BLANK_CD)
    mod.disc_info = lambda node, run=None: dict(info)
    d.app._rescan()
    d.pump(0.5)
    return d, mod


def add(d, path, wait=20.0):
    """Add one file and WAIT for its length probe to land.

    The probe runs on a worker (an app that freezes while ffprobe reads a file
    off a disc is the defect that put it there), so the row does not exist
    until the job's callback returns. A short wait made this whole file flaky
    on a loaded machine — the item was simply missing and half a dozen checks
    below failed for a reason that had nothing to do with what they test — so
    the wait is generous and a timeout SAYS so rather than passing quietly.
    """
    nbpicker.open_file = lambda *a, **k: path
    d.app.add_btn.clicked()
    end = time.time() + wait
    while time.time() < end and d.app._add_pending:
        d.pump(0.1)
    d.pump(0.05)
    if d.app._add_pending:
        print("NOTE add(%s): still probing after %.0fs"
              % (os.path.basename(path), wait), flush=True)


def wait_idle(d, limit=30.0):
    end = time.time() + limit
    while time.time() < end:
        d.pump(0.2)
        if not d.app.busy:
            return True
    return False


def notifs(d):
    root = os.path.join(d.home, ".config", "notebook", "notifications")
    out = []
    for path in sorted(glob.glob(os.path.join(root, "*.json"))):
        try:
            out.append(json.load(io.open(path, encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return out


def clear_notifs(d):
    root = os.path.join(d.home, ".config", "notebook", "notifications")
    for path in glob.glob(os.path.join(root, "*.json")):
        os.remove(path)


def clear_marks():
    for path in glob.glob(os.path.join(MARK, "*")):
        os.remove(path)


def on_screen(d, widget):
    """(top, bottom) of `widget` in panel coordinates."""
    d.off.check_resize()
    d.pump(0.1)
    holder = d.off.get_child()
    where = widget.translate_coordinates(holder, 0, 0)
    if not where:
        return None
    return where[1], where[1] + widget.get_allocation().height


WRITTEN = "The disc is written. It can be taken out."


# ======================================================= 1. a burn happens
def burns():
    section("1. the burn runs")

    # The contract, in isolation: nbjobs.Job.cancelled is a PROPERTY (it
    # delegates to the cancel token's own property), so a poll loop that calls
    # it raises TypeError on the worker thread and the burn dies before the
    # first track. Driven against a REAL Job, not a fake with a method on it —
    # that fake is exactly how this shipped.
    job = nbjobs.Job(nbjobs.JobOwner(name="realuse"), "burn", 0,
                     lambda _j: None)
    try:
        out = burner._step(job, ["/bin/sh", "-c",
                                 "echo one; sleep 0.6; echo two"], "checking")
        ok, detail = ("one" in out and "two" in out), out
    except Exception as exc:                                  # noqa: BLE001
        ok, detail = False, "%s: %s" % (type(exc).__name__, exc)
    check("a step polls cancellation the way nbjobs.Job reports it", ok,
          detail)

    clear_marks()
    d, _mod = make()
    add(d, SONG)
    add(d, SONG2)
    ANSWER[0] = Gtk.ResponseType.OK
    clear_notifs(d)
    d.app.go_btn.clicked()
    idle = wait_idle(d)
    status = d.app.status.get_text()
    check("an audio CD burn reaches the drive and says the disc is written",
          idle and status == WRITTEN
          and os.path.exists(os.path.join(MARK, "wodim.ran")),
          "idle=%s status=%r wodim ran=%s"
          % (idle, status, os.path.exists(os.path.join(MARK, "wodim.ran"))))
    d.shot(os.path.join(WORK, "audio-burn-done.png"))
    d.close()

    clear_marks()
    d, _mod = make(disc=BLANK_DVD)
    d.app.video_btn.clicked()
    d.pump(0.2)
    add(d, CLIP)
    add(d, CLIP2)
    ANSWER[0] = Gtk.ResponseType.OK
    d.app.go_btn.clicked()
    idle = wait_idle(d, 40)
    status = d.app.status.get_text()
    check("a video DVD burn reaches the drive and says the disc is written",
          idle and status == WRITTEN
          and os.path.exists(os.path.join(MARK, "growisofs.ran")),
          "idle=%s status=%r growisofs ran=%s"
          % (idle, status, os.path.exists(os.path.join(MARK,
                                                      "growisofs.ran"))))
    d.close()


# ======================================================= 2. the File menu
def file_menu():
    section("2. File > Add… and File > Remove All")
    d, _mod = make()
    add(d, SONG)
    add(d, SONG2)
    before = len(d.app.items)
    nbpicker.open_file = lambda *a, **k: SONG3
    layer = d.open_menu("File")
    for w in d.walk(layer):
        if isinstance(w, Gtk.Button) and (w.get_label() or "").startswith("Add"):
            w.clicked()
            break
    end = time.time() + 2.0
    while time.time() < end and d.app._add_pending:
        d.pump(0.1)
    check("File > Add… adds a track, like the button beside it",
          len(d.app.items) == before + 1,
          "%d -> %d" % (before, len(d.app.items)))

    layer = d.open_menu("File")
    for w in d.walk(layer):
        if isinstance(w, Gtk.Button) and (w.get_label() or "").startswith(
                "Remove All"):
            w.clicked()
            break
    d.pump(0.2)
    check("File > Remove All empties the list", not d.app.items,
          [it["name"] for it in d.app.items])
    d.close()


# ======================================================= 3. the status line
def status_on_screen():
    section("3. the status line is on the smallest panel, in both modes")
    for w, h in ((1024, 740), (1366, 740)):
        d, _mod = make(disc=BLANK_DVD, size=(w, h))
        for mode, btn in (("Music CD", d.app.audio_btn),
                          ("Video DVD", d.app.video_btn)):
            btn.clicked()
            d.pump(0.2)
            d.app._say("The disc is written. It can be taken out.")
            # The worst case a person can put on screen: a wrapped warning
            # under the meter, which is what the burn refusals sit beside.
            d.app.warn.set_text(
                "That is more video than one DVD holds at a watchable "
                "quality. Remove a video or two.")
            box = on_screen(d, d.app.status)
            d.shot(os.path.join(WORK, "status-%dx%d-%s.png"
                                % (w, h, mode.split()[0])))
            check("the status line is on screen in %s mode at %dx%d"
                  % (mode, w, h),
                  box is not None and box[1] <= h,
                  "top/bottom %r in a %d-high panel" % (box, h))
        d.close()


# ======================================================= 4. the chosen row
def chosen_row():
    section("4. the chosen row looks chosen")
    from PIL import Image, ImageChops
    d, _mod = make()
    for path in (SONG, SONG2, SONG3):
        add(d, path)
    before = d.shot(os.path.join(WORK, "rows-none.png"))
    d.app._row_buttons[1].clicked()
    d.pump(0.2)
    after = d.shot(os.path.join(WORK, "rows-second.png"))
    # choosing a row re-renders the list, so the row to measure is the new one
    row = d.app._row_buttons[1]
    alloc = row.get_allocation()
    where = row.translate_coordinates(d.off.get_child(), 0, 0)
    if not where:
        check("choosing a row changes the pixels of that row", False,
              "the row is not on screen")
        d.close()
        return
    a = Image.open(before).convert("RGB")
    b = Image.open(after).convert("RGB")
    box = (where[0], where[1], where[0] + alloc.width,
           where[1] + alloc.height)
    diff = ImageChops.difference(a.crop(box), b.crop(box)).getbbox()
    check("choosing a row changes the pixels of that row",
          diff is not None and row.get_active(),
          "row box %r diff %r active=%s" % (box, diff, row.get_active()))
    d.close()


# ======================================================= 5. switching mode
def mode_lists():
    section("5. switching mode keeps what was assembled")
    d, _mod = make()
    for path in (SONG, SONG2, SONG3):
        add(d, path)
    songs = [it["name"] for it in d.app.items]
    d.app.video_btn.clicked()
    d.pump(0.3)
    empty = [it["name"] for it in d.app.items]
    add(d, CLIP)
    videos = [it["name"] for it in d.app.items]
    d.app.audio_btn.clicked()
    d.pump(0.3)
    back = [it["name"] for it in d.app.items]
    check("switching to Video DVD and back keeps the songs",
          len(songs) == 3 and back == songs, (songs, back))
    check("each mode keeps its own list", empty == [] and videos == [
        os.path.splitext(os.path.basename(CLIP))[0]], (empty, videos))
    d.app.video_btn.clicked()
    d.pump(0.3)
    check("the videos are still there too", [it["name"] for it in d.app.items]
          == videos, [it["name"] for it in d.app.items])
    d.close()


# ======================================================= 6. the wrong disc
def wrong_disc():
    section("6. a disc that cannot be written says why")
    d, _mod = make(disc=BLANK_DVD)          # a DVD, in Music CD mode
    add(d, SONG)
    ANSWER[0] = Gtk.ResponseType.CANCEL
    d.app.go_btn.clicked()
    d.pump(0.2)
    said = d.app.status.get_text()
    d.shot(os.path.join(WORK, "wrong-medium.png"))
    check("a disc of the wrong kind is refused with the reason and the remedy",
          said not in ("Music CD", "Video DVD") and "CD-R" in said
          and len(said.split()) > 6, repr(said))
    d.close()

    d, _mod = make(disc={"media": "CD-RW", "blank": False, "present": True,
                         "bytes": 700 * 1000 * 1000})
    add(d, SONG)
    d.app.go_btn.clicked()
    d.pump(0.2)
    said = d.app.status.get_text()
    check("a disc that has already been written is told apart from a wrong one",
          "written" in said.lower() and "blank" in said.lower(), repr(said))
    d.close()


# ======================================================= 7. one is one
def one_is_one():
    section("7. one song is one song")
    ANSWER[0] = Gtk.ResponseType.CANCEL
    d, _mod = make()
    add(d, SONG)
    del DIALOGS[:]
    d.app.go_btn.clicked()
    d.pump(0.2)
    card = " | ".join(DIALOGS[0]) if DIALOGS else ""
    check("one song reads as one song on the confirmation card",
          "1 songs" not in card and "1 song will be written" in card,
          repr(card))
    d.close()

    d, _mod = make(disc=BLANK_DVD)
    d.app.video_btn.clicked()
    d.pump(0.2)
    add(d, CLIP)
    del DIALOGS[:]
    d.app.go_btn.clicked()
    d.pump(0.2)
    card = " | ".join(DIALOGS[0]) if DIALOGS else ""
    check("one video reads as one video on the confirmation card",
          "1 videos" not in card and "1 video will be converted" in card,
          repr(card))
    d.close()


# ======================================== 8. controls during a write, 12. Esc
def during_the_write():
    section("8. what a running burn lets you press (and 12. Escape)")
    d, _mod = make()
    for path in (SONG, SONG2, SONG3):
        add(d, path)
    ANSWER[0] = Gtk.ResponseType.OK
    d.app.go_btn.clicked()
    d.pump(0.4)
    removes = [w for w in d.walk()
               if isinstance(w, Gtk.Button) and w.get_label() == "Remove"]
    d.shot(os.path.join(WORK, "during-burn.png"))
    check("Remove all and every row's Remove are disabled while writing",
          d.app.busy and not d.app.clear_btn.get_sensitive()
          and removes and not any(w.get_sensitive() for w in removes),
          "busy=%s remove all=%s rows=%r"
          % (d.app.busy, d.app.clear_btn.get_sensitive(),
             [w.get_sensitive() for w in removes]))

    del DIALOGS[:]
    ANSWER[0] = Gtk.ResponseType.CANCEL
    d.key("Escape")
    d.pump(0.3)
    card = " | ".join(DIALOGS[0]) if DIALOGS else ""
    check("closing during a write says a disc is being written",
          "writ" in card.lower() and "part-written" in card.lower()
          and card.count("[Stop]") == 0, repr(card))
    check("declining that card leaves the burn running", d.app.busy)
    wait_idle(d, 30)
    check("the controls come back when the burn ends",
          d.app.clear_btn.get_sensitive() and d.app.add_btn.get_sensitive(),
          "remove all=%s add=%s" % (d.app.clear_btn.get_sensitive(),
                                    d.app.add_btn.get_sensitive()))
    d.close()


# ======================================================= 9. a bad file
def bad_files():
    section("9. a file the app cannot read")
    d, _mod = make()
    add(d, SONG)
    add(d, UNREADABLE)
    warn = d.app.warn.get_text()
    d.shot(os.path.join(WORK, "unreadable-listed.png"))
    check("a list holding an unreadable file cannot start a burn",
          not d.app.go_btn.get_sensitive(),
          "items %r" % [(it["name"], it["seconds"]) for it in d.app.items])
    check("and the warning names the file to remove",
          "UNREADABLE song" in warn, repr(warn))
    d.close()

    clear_marks()
    d, _mod = make()
    add(d, SONG)
    add(d, BADFILE)          # probes fine, then the decoder refuses it
    ANSWER[0] = Gtk.ResponseType.OK
    clear_notifs(d)
    d.app.go_btn.clicked()
    wait_idle(d, 30)
    said = d.app.status.get_text()
    check("a file that fails to convert is named, not the tool",
          "BADFILE song.wav" in said and "ffmpeg" not in said, repr(said))
    check("and nothing was written to the disc",
          not os.path.exists(os.path.join(MARK, "wodim.ran")))
    d.close()


# ======================================================= 10. the tray line
def tray_line():
    section("10. the failure notification")
    d, mod = make()
    add(d, SONG)

    def boom(*_a, **_k):
        raise ValueError("something with no sentence of its own")

    mod.build_audio_cd = boom
    ANSWER[0] = Gtk.ResponseType.OK
    clear_notifs(d)
    d.app.go_btn.clicked()
    wait_idle(d, 20)
    posted = notifs(d)
    last = posted[-1] if posted else {}
    check("a failure with no sentence of its own does not repeat its headline",
          bool(posted) and last.get("title") and last.get("body") != last.get(
              "title"), last)
    d.close()


# ======================================================= 11. what it says
def plain_words():
    section("11. the meter and the disc name")
    d, _mod = make(disc=BLANK_DVD)
    d.app.video_btn.clicked()
    d.pump(0.2)
    add(d, CLIP)
    add(d, CLIP2)
    meter = d.app.meter_lbl.get_text()
    check("the video meter says how the disc will look, not its bitrate",
          "kbit" not in meter and "of video" in meter, repr(meter))

    d.app.name_entry.set_text("   ")
    d.pump(0.2)
    hint = getattr(d.app, "name_hint", None)
    blank_hint = hint.get_text() if hint is not None else "<no hint label>"
    d.app.name_entry.set_text("Holiday")
    d.pump(0.2)
    named_hint = hint.get_text() if hint is not None else "<no hint label>"
    check("a blank disc name says the name the disc will actually get",
          "My Disc" in blank_hint and "My Disc" not in named_hint,
          (blank_hint, named_hint))
    d.shot(os.path.join(WORK, "video-meter.png"))
    d.close()


# ======================================================= 13. naming the disc
def naming_the_disc():
    """Typing a name while a title is chosen, and the keyboard flow that
    costs.

    _refresh rebuilds every row, which destroys the row widget holding the
    keyboard, so the chosen row takes focus back afterwards. But _refresh also
    runs on every keystroke in the DISC NAME field (its "changed" handler):
    taking focus back from THERE left the disc unnameable — the first letter
    typed moved the keyboard to the chosen title and the rest went nowhere.
    Both halves are checked, because a fix that simply drops the restore
    would leave a keyboard user with no focus at all after Move up/down.
    """
    section("13. typing a disc name with a title chosen")
    d, _mod = make(disc=BLANK_DVD)
    d.app.video_btn.clicked()
    d.pump(0.2)
    add(d, CLIP)
    add(d, CLIP2)
    d.app._row_buttons[0].clicked()
    d.pump(0.2)
    d.app.name_entry.grab_focus()
    d.pump(0.1)
    d.type("Holiday 2026")
    d.pump(0.2)
    typed = d.app.name_entry.get_text()
    check("typing a disc name with a title chosen keeps every letter",
          typed == "Holiday 2026" and d.off.get_focus() is d.app.name_entry,
          "entry=%r focus=%s" % (typed, type(d.off.get_focus()).__name__))
    d.shot(os.path.join(WORK, "disc-name-typed.png"))

    row = d.app._row_buttons[0]
    row.grab_focus()
    d.pump(0.1)
    on_row = d.off.get_focus() is row
    d.app.move_down_btn.clicked()
    d.pump(0.2)
    chosen = d.app._row_buttons.get(d.app._sel)
    check("moving a chosen row keeps the keyboard on that row",
          on_row and d.app._sel == 1 and d.off.get_focus() is chosen,
          "was on row=%s sel=%s focus=%s"
          % (on_row, d.app._sel, type(d.off.get_focus()).__name__))
    d.close()


SECTIONS = (burns, file_menu, status_on_screen, chosen_row, mode_lists,
            wrong_disc, one_is_one, during_the_write, bad_files, tray_line,
            plain_words, naming_the_disc)

# Named on the command line, one or more sections run alone — which is how a
# fix is proved: revert it, run its section, watch its check fail BY NAME.
wanted = [a for a in sys.argv[1:] if not a.startswith("-")]
for fn in SECTIONS:
    if not wanted or fn.__name__ in wanted:
        run(fn)

print("\n%d checks, %d passed, %d FAILED"
      % (CHECKS[0], CHECKS[0] - len(FAILS), len(FAILS)))
if FAILS:
    print("RESULT: FAILED")
    for name in FAILS:
        print("   - %s" % name)
else:
    print("RESULT: ALL PASS")
sys.exit(len(FAILS))
