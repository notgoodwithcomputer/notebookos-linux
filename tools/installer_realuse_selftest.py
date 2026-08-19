#!/usr/bin/env python3
"""Real-use regression drive for Install Notebook OS, on the real widget tree.

Every check here is something a person meets on the screen — a control they
cannot use, a sentence that stopped being true, a report that opens at the
wrong end — driven through tools/appdrive on an offscreen holder at the
1024x740 panel, with the live medium and the disk list faked so the whole
wizard can be walked without a disk to erase. The destructive engine is driven
to a REAL failure with the command runner replaced, so nothing outside this
process is touched. Every check is named; a check fails by name, never by
crash.

  IN-1 unavailable reads as unavailable   a control that has been greyed out
       (a disk too small to install onto, the identity rows deferred by "Set
       this up for someone else", the password rows the passwordless tick
       makes moot, the spare-memory size) must not be pixel-identical to the
       same control live. Measured as ink, not read off the CSS: the app's own
       rules sit at APPLICATION+1 and beat the theme's insensitive styling, so
       a rule that LOOKS right and never matches the node that draws the text
       leaves the control black.
  IN-2 the report opens where the failure is   the transcript is appended
       while it is folded away, where the box has no size; it used to open at
       its first line with the failing command off the bottom.
  IN-3 the password note sits above its card   the code's own comment says the
       sentence belongs above both password rows; packed after the card it
       landed flush under the card's bottom edge, captioning the tick that
       means no password is ever asked for.
  IN-4 the progress page says the run stopped   "Installing / keep the
       computer switched on" stayed on screen over a stopped install.
  IN-4b a second attempt says it is installing again   the same two labels,
       put back by _reset_progress, so a retry is not narrated by the words of
       the run that stopped.
  IN-5 the Summary after a failed run          "Nothing is written until it is
       confirmed", about a disk the engine had already erased.
  IN-6 one verb for switching the machine off  a button saying Shut Down that
       opens a dialog saying Switch off is two actions to the person reading.
  IN-7 an error names the field on the screen  the box is labelled Computer
       name; the message refusing it said hostname.
  IN-8 the review is written in one case       one lowercase fragment in a
       column of sentences.
  IN-9 the first screen does not say itself twice.

Run under the guest theme:
  NB_DRIVE_HOME_ROOT=<scratch> tools/guestrun.sh python3 \\
      tools/installer_realuse_selftest.py
"""
import os
import re
import sys
import shutil
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ.setdefault("NB_DRIVE_HOME_ROOT",
                      tempfile.mkdtemp(prefix="installer-realuse-"))

import cairo                                                       # noqa: E402
import appdrive                                                    # noqa: E402
from gi.repository import Gtk                                      # noqa: E402

RESULTS = []
SHOTS = os.path.join(os.environ["NB_DRIVE_HOME_ROOT"], "shots")
os.makedirs(SHOTS, exist_ok=True)


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name
          + (("  -- " + detail) if (detail and not cond) else ""))


# ---------------------------------------------------------------- fixtures
FAKE_TAR = "/run/live/medium/install/rootfs.tar"
FAKE_TOOLS = ("sgdisk", "wipefs", "mkfs.vfat", "mkfs.ext4", "mkswap",
              "lsblk", "findmnt", "blkid", "partx", "partprobe",
              "mount", "umount", "tar", "sync", "udevadm", "reboot",
              "poweroff")
GB = 1024 ** 3
# (name, size, model, contents) — what _populate_disks unpacks. The 2 GB stick
# cannot hold the 3.6 GB payload, so its row is the one the app greys out.
DISKS = [("fakeA", 2 * GB, "Tiny Stick", "Files"),
         ("fakeB", 240 * GB, "Acme SSD 240G", "Windows")]


def fake_medium():
    """Make the installer believe it is running on a live medium with every
    tool present. Returns a restore() callable."""
    real_exists, real_getsize, real_which = (os.path.exists, os.path.getsize,
                                             shutil.which)

    def exists(p):
        return True if p == FAKE_TAR else real_exists(p)

    def getsize(p):
        return 3600 * 1024 * 1024 if p == FAKE_TAR else real_getsize(p)

    def which(name, *a, **k):
        return "/usr/bin/" + name if name in FAKE_TOOLS else real_which(name, *a, **k)
    os.path.exists, os.path.getsize, shutil.which = exists, getsize, which

    def restore():
        os.path.exists, os.path.getsize, shutil.which = (real_exists,
                                                         real_getsize,
                                                         real_which)
    return restore


# ------------------------------------------------------------------ pixels
class Img:
    def __init__(self, path):
        self.s = cairo.ImageSurface.create_from_png(path)
        self.w, self.h = self.s.get_width(), self.s.get_height()
        self.stride = self.s.get_stride()
        self.d = bytes(self.s.get_data())

    def px(self, x, y):
        o = y * self.stride + x * 4
        return (self.d[o + 2], self.d[o + 1], self.d[o])

    def darkest(self, box):
        x, y, w, h = box
        best = (255, 255, 255)
        for yy in range(max(0, y), min(self.h, y + h)):
            for xx in range(max(0, x), min(self.w, x + w)):
                p = self.px(xx, yy)
                if sum(p) < sum(best):
                    best = p
        return best


def rect(d, w):
    """(x, y, width, height) of widget w in the coordinates a shot uses."""
    clamp = d.off.get_child()
    a = w.get_allocation()
    xy = w.translate_coordinates(clamp, 0, 0)
    return None if xy is None else (xy[0], xy[1], a.width, a.height)


def render(d, name):
    """A shot with the SCROLLING page drawn at its own offset.

    The offscreen holder does not paint a viewport's off-screen part, so a
    page scrolled down comes back blank in a plain shot. Draw the page's
    column into the shot at the scroll position the user is looking at.
    """
    path = os.path.join(SHOTS, name + ".png")
    base = d.shot(os.path.join(SHOTS, name + "_raw.png"))
    page = d.app.stack.get_visible_child()
    if not isinstance(page, Gtk.ScrolledWindow):
        os.replace(os.path.join(SHOTS, name + "_raw.png"), path)
        return path
    adj = page.get_vadjustment()
    vp = page.get_child()
    col = vp.get_child()
    surf = cairo.ImageSurface.create_from_png(base)
    cr = cairo.Context(surf)
    vx, vy, vw, vh = rect(d, vp)
    cr.save()
    cr.rectangle(vx, vy, vw, vh)
    cr.clip()
    cr.set_source_rgb(0xFC / 255, 0xFB / 255, 0xF8 / 255)
    cr.paint()
    cr.translate(vx, vy - adj.get_value())
    col.draw(cr)
    cr.restore()
    surf.flush()
    surf.write_to_png(path)
    return path


INK = 200        # sum(rgb) at or below this is live ink
MUTED = 380      # sum(rgb) at or above this is unavailable


def ink_check(name, live_px, off_px, detail=""):
    """A greyed control must be lighter than the same control live — and the
    live one must actually be ink, so the pair can never agree by both being
    pale."""
    check(name,
          sum(live_px) <= INK and sum(off_px) >= MUTED,
          "live=%s off=%s %s" % (live_px, off_px, detail))


def labels(d, cls, root=None):
    root = root if root is not None else d.app.stack.get_visible_child()
    return [w for w in d.walk(root) if isinstance(w, Gtk.Label)
            and cls in w.get_style_context().list_classes()]


def words(text):
    return re.findall(r"[a-z0-9]+", text.lower())


# ------------------------------------------------------------------ drive A
def t_a_options_and_disk_list():
    restore = fake_medium()
    d = appdrive.Drive("installer")
    try:
        app = d.app
        app._list_disks = lambda: list(DISKS)

        # IN-9 — the first screen anyone sees must not spend its first line
        # repeating the line above it. Four words running in both is a
        # restatement, whatever the words are.
        sub = labels(d, "inst-sub")[0].get_text()
        para = labels(d, "inst-para")[0].get_text()
        sw, pw = words(sub), words(para)
        shared = set()
        for i in range(len(sw) - 3):
            gram = tuple(sw[i:i + 4])
            for j in range(len(pw) - 3):
                if tuple(pw[j:j + 4]) == gram:
                    shared.add(" ".join(gram))
        check("IN-9 the welcome page does not repeat its own subtitle",
              not shared, "repeated: %s" % sorted(shared))

        d.click("Next")
        d.pump(0.9)
        # IN-1a — the disk that cannot be chosen, beside one that can, in the
        # same picture.
        im = Img(render(d, "in1_disks"))
        rows = {}
        for w in d.walk():
            if isinstance(w, Gtk.RadioButton) and "fake" in (w.get_label() or ""):
                row = w.get_parent().get_parent()
                rows[row.get_sensitive()] = (w.get_label(), rect(d, row))
        ok = True in rows and False in rows
        if not ok:
            check("IN-1a a disk too small to install onto reads as unavailable",
                  False, "rows found: %r" % rows)
        else:
            ink_check("IN-1a a disk too small to install onto reads as "
                      "unavailable",
                      im.darkest(rows[True][1]), im.darkest(rows[False][1]),
                      "%r vs %r" % (rows[True][0], rows[False][0]))

        [w for w in d.find(Gtk.RadioButton)
         if "fakeB" in (w.get_label() or "")][0].set_active(True)
        d.pump(0.3)
        d.click("Next")
        d.pump(0.7)
        page = app.stack.get_visible_child()
        adj = page.get_vadjustment()

        # IN-3 — where the password note is drawn.
        note = [w for w in d.walk() if isinstance(w, Gtk.Label)
                and w.get_text().startswith("This password is asked")][0]
        card = app._e_pw.get_parent().get_parent()
        head = [w for w in d.walk() if isinstance(w, Gtk.Label)
                and w.get_text() == "Administrator password"][0]
        adj.set_value(adj.get_upper())
        d.pump(0.3)
        render(d, "in3_note")
        rn, rc, rh = rect(d, note), rect(d, card), rect(d, head)
        check("IN-3 the password note sits above the password card",
              rn[1] + rn[3] <= rc[1] and rc[1] - (rn[1] + rn[3]) > 0
              and rn[1] >= rh[1],
              "heading y=%d note y=%d..%d card y=%d..%d"
              % (rh[1], rn[1], rn[1] + rn[3], rc[1], rc[1] + rc[3]))

        # IN-1b — the rows "Set this up for someone else" defers.
        adj.set_value(0)
        d.pump(0.2)
        app._e_user.set_text("Ada")
        d.pump(0.3)
        live = Img(render(d, "in1_oem_off"))
        r_entry, r_combo = rect(d, app._e_user), rect(d, app._c_kbd)
        r_lbl = [rect(d, w) for w in d.walk()
                 if isinstance(w, Gtk.Label) and w.get_text() == "Name"][0]
        live_px = (live.darkest(r_entry), live.darkest(r_lbl),
                   live.darkest(r_combo))
        app._cb_oem.set_active(True)
        d.pump(0.5)
        off = Img(render(d, "in1_oem_on"))
        off_px = (off.darkest(rect(d, app._e_user)), off.darkest(r_lbl),
                  off.darkest(rect(d, app._c_kbd)))
        for tag, a, b in (("name box", live_px[0], off_px[0]),
                          ("its label", live_px[1], off_px[1]),
                          ("keyboard drop-down", live_px[2], off_px[2])):
            ink_check("IN-1b a row deferred to the new owner reads as "
                      "unavailable (%s)" % tag, a, b)
        app._cb_oem.set_active(False)
        d.pump(0.4)

        # IN-1c — the password rows the passwordless tick makes moot.
        app._e_pw.set_text("secret")
        app._e_pw2.set_text("secret")
        adj.set_value(adj.get_upper())
        d.pump(0.3)
        live = Img(render(d, "in1_pw_on"))
        r_pw = rect(d, app._e_pw)
        r_pwl = [rect(d, w) for w in d.walk()
                 if isinstance(w, Gtk.Label) and w.get_text() == "Password"][0]
        live_px = (live.darkest(r_pw), live.darkest(r_pwl))
        app._chk_rootless.set_active(True)
        d.pump(0.5)
        off = Img(render(d, "in1_pw_off"))
        off_px = (off.darkest(rect(d, app._e_pw)), off.darkest(r_pwl))
        for tag, a, b in (("password box", live_px[0], off_px[0]),
                          ("its label", live_px[1], off_px[1])):
            ink_check("IN-1c a password row switched off reads as "
                      "unavailable (%s)" % tag, a, b)
        app._chk_rootless.set_active(False)
        d.pump(0.4)

        # IN-1d — the spare-memory size, greyed until the tick above it is on.
        r_spin = rect(d, app._sp_swap)
        off = Img(render(d, "in1_swap_off"))
        app._chk_swap.set_active(True)
        d.pump(0.4)
        live = Img(render(d, "in1_swap_on"))
        ink_check("IN-1d the spare-memory size reads as unavailable until it "
                  "is asked for",
                  live.darkest(rect(d, app._sp_swap)), off.darkest(r_spin))
        app._chk_swap.set_active(False)
        d.pump(0.3)

        # IN-7 — the message refusing a field calls that field what its own
        # label calls it. Taken from the widget, not from a literal, so the
        # two can never drift apart: rename the row and the message that
        # refuses it has to be renamed with it.
        app._e_host.set_text("my computer!")
        d.pump(0.4)
        msg = app._opt_hint.get_text()
        field = [w.get_text() for w in d.walk(app._e_host.get_parent())
                 if isinstance(w, Gtk.Label)
                 and "inst-label" in w.get_style_context().list_classes()][0]
        check("IN-7 the computer-name error names the field on the screen",
              bool(msg) and field.lower() in msg.lower(),
              "field=%r message=%r" % (field, msg))
    finally:
        restore()
        d.close()


# ------------------------------------------------------------------ drive B
def t_b_a_run_that_stops():
    restore = fake_medium()
    d = appdrive.Drive("installer")
    try:
        app = d.app
        import installer as I
        app._list_disks = lambda: list(DISKS)
        d.click("Next")
        d.pump(0.9)
        [w for w in d.find(Gtk.RadioButton)
         if "fakeB" in (w.get_label() or "")][0].set_active(True)
        d.pump(0.3)
        d.click("Next")
        d.pump(0.6)
        app._e_user.set_text("Ada")
        app._e_pw.set_text("secret")
        app._e_pw2.set_text("secret")
        d.pump(0.4)
        d.click("Next")
        d.pump(0.9)

        # IN-8 — one case down the review's value column. Only the values the
        # APP writes: a computer name somebody typed in lower case is theirs,
        # not a sentence of ours to capitalise.
        vals = [w.get_text() for w in labels(d, "inst-value")]
        typed = [t for t in (app.cfg.get("username"), app.cfg.get("hostname"),
                             app.cfg.get("disk_model"), app.cfg.get("disk"))
                 if t]
        ours = [v for v in vals if not any(t in v for t in typed)]
        odd = [v for v in ours if v and not (v[0].isupper() or v[0].isdigit())]
        check("IN-8 every value the review writes itself starts as a sentence",
              ours and not odd, "lower-case values: %r" % odd)

        # A real failure: the engine's own path, with only the command runner
        # replaced, so nothing outside this process is touched.
        def fake_sh(argv, allow_fail=False):
            app._post_log("$ " + " ".join(argv))
            tool = os.path.basename(argv[0])
            for i in range(4):
                app._post_log("  %s: working on %s (line %d)"
                              % (tool, argv[-1], i))
            if tool == "mkfs.ext4":
                app._post_log("%s: %s2: Input/output error"
                              % (tool, app.cfg["disk"]))
                raise I.InstallError("%s exited with status 1" % tool)
            return 0
        app._sh = fake_sh
        app._wait_for = lambda p, timeout=25.0: True
        app._unmount_target = lambda disk, umount: None
        d.click("Erase disk and install")
        d.pump(0.5)
        d.click("Erase and install")
        d.pump(3.0)
        render(d, "in2_failed")

        # IN-2 — the report opens at the command that failed.
        adj = app._log_scroll.get_vadjustment()
        buf = app._log_buf
        tail = buf.get_text(buf.get_start_iter(), buf.get_end_iter(),
                            False).strip().split("\n")[-1]
        scrolls = adj.get_upper() > adj.get_page_size() + 10
        at_end = adj.get_value() + adj.get_page_size() >= adj.get_upper() - 2
        check("IN-2 the report opens at the command that failed",
              app._log_toggle.get_active() and scrolls and at_end
              and "Input/output error" in tail,
              "open=%s value=%.0f page=%.0f upper=%.0f last=%r"
              % (app._log_toggle.get_active(), adj.get_value(),
                 adj.get_page_size(), adj.get_upper(), tail))

        # IN-4 — the page's own words follow the state.
        h1 = [w.get_text() for w in labels(d, "inst-h1")]
        sub = [w.get_text() for w in labels(d, "inst-sub")]
        stale = [t for t in (h1 + sub)
                 if "Installing" in t or "keep the computer switched on" in t]
        check("IN-4 the progress page says the run stopped",
              h1 == ["Installation stopped"] and not stale,
              "heading=%r subtitle=%r" % (h1, sub))

        # IN-5 — and so does the Summary the failure page sends them back to.
        d.click("Back")
        d.pump(0.9)
        render(d, "in5_summary_after_fail")
        sub = " ".join(w.get_text() for w in labels(d, "inst-sub"))
        danger = app._summary_danger.get_text()
        check("IN-5 the Summary after a failed run says the disk is erased",
              app.STEPS[app._step][0] == "summary"
              and "Nothing is written" not in sub
              and "already been erased" in (sub + " " + danger)
              and "will be erased" not in danger,
              "subtitle=%r danger=%r" % (sub, danger))

        # IN-4b — and the words come BACK. The heading and subtitle are state
        # now, so the page that has said "Installation stopped" must not still
        # be saying it over a run that has just been started again. Driven
        # through the real confirmation, with the engine's first command held
        # on an event so the page is read while the run is genuinely alive.
        held = threading.Event()
        started = {"n": 0}

        def held_sh(argv, allow_fail=False):
            started["n"] += 1
            if started["n"] == 1:
                app._post_log("$ " + " ".join(argv))
                held.wait(20)
            return fake_sh(argv, allow_fail)
        app._sh = held_sh
        d.click("Erase disk and install")
        d.pump(0.4)
        d.click("Erase and install")
        d.pump(1.0)
        h1 = [w.get_text() for w in labels(d, "inst-h1")]
        sub = [w.get_text() for w in labels(d, "inst-sub")]
        held.set()
        d.pump(2.5)
        check("IN-4b a second attempt says it is installing again",
              app.STEPS[app._step][0] == "progress" and h1 == ["Installing"]
              and any("keep the computer switched on" in t for t in sub),
              "heading=%r subtitle=%r" % (h1, sub))

        # IN-6 — one verb for one action, from the button to the dialog.
        app._set_step(app._steps_index("done"))
        d.pump(0.5)
        btn = app._done_shut.get_label()
        app._done_shut.clicked()
        d.pump(0.5)
        found = []

        def walk(w):
            found.append(w)
            if isinstance(w, Gtk.Container):
                for c in w.get_children():
                    walk(c)
        walk(app._confirm_layer)
        heading = [w.get_text() for w in found if isinstance(w, Gtk.Label)][0]
        oks = [w.get_label() for w in found if isinstance(w, Gtk.Button)]
        ok = [t for t in oks if t and t.lower() != "cancel"][0]
        verb = btn.lower().strip(" .?…")
        check("IN-6 one verb for switching the machine off",
              heading.lower().startswith(verb) and ok.lower() == verb,
              "button=%r heading=%r confirm=%r" % (btn, heading, ok))
    finally:
        restore()
        d.close()


for fn in (t_a_options_and_disk_list, t_b_a_run_that_stops):
    try:
        fn()
    except Exception as exc:                                      # noqa: BLE001
        check("%s ran without raising" % fn.__name__, False,
              "%s: %s" % (type(exc).__name__, exc))

bad = [n for n, ok in RESULTS if not ok]
print("RESULT: %s (%d checks, %d failed)"
      % ("PASS" if not bad else "FAILED", len(RESULTS), len(bad)))
raise SystemExit(1 if bad else 0)
