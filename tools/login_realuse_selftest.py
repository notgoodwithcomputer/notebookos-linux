#!/usr/bin/env python3
"""Real-use regression drive for the sign-in / lock screen, on the real tree.

Every check below is something a person did at this screen and got the wrong
answer for, driven the way they did it — typing, ticking the box, getting the
password wrong three times, looking for a way to stop the computer — through
tools/appdrive on an offscreen holder at the smallest panel the OS supports.
Each check is named and fails BY NAME, never by crash, so a method that has
gone missing reads as the defect it is rather than as a traceback.

  show password keeps caret  ticking "Show password" mid-password selected the
                             whole field (Gtk.Entry.grab_focus() selects even
                             when the entry already had the caret), so the next
                             key replaced it: "secret" + tick + "X" gave "X",
                             and on the untick the field is masked again so
                             nothing on screen showed what was lost
  date order                 the date under the clock was built word by word
                             into a positional catalog pattern, which cannot
                             reorder: a Japanese machine's first screen read
                             "月曜日、17日 8月" while its own panel clock read
                             "8月17日 月曜日"
  the column does not move   the first wrong password lifted the clock, the
                             name and the field by 44px, and the next keystroke
                             dropped them 11px back — the screen reflowing
                             under the caret at the moment somebody is told
                             they were wrong — and the field itself changed
                             width with whatever was longest beneath it
  the pause says so          after three failures the field stops accepting
                             keys for a few seconds and looked EXACTLY like a
                             field that accepts them (measured: the same
                             bytes), with nothing to say to wait
  a way to stop the computer there were no power controls on either surface, so
                             a forgotten password — or a lock screen whose
                             credential file cannot be read, where nothing
                             typed can ever be accepted — left the mains switch
  two answers, two paragraphs the keyboard sentence and the recall were joined
                             by one "\n" in one centred label and read as a
                             single run-on block
  no one-word last line      "...Notebook OS was / installed."
  a long name ellipsizes     a 49-character name was cut mid-word at 40

Run under the guest theme:
  NB_DRIVE_HOME_ROOT=<scratch> tools/guestrun.sh python3 \
      tools/login_realuse_selftest.py

Exit status is the number of failed checks.
"""
import os
import sys
import time
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, os.path.join(ROOT, "tools"))
WORK = tempfile.mkdtemp(prefix="login-realuse-")
os.environ.setdefault("NB_DRIVE_HOME_ROOT", os.path.join(WORK, "home"))

import appdrive                                                   # noqa: E402
import uishot                                                     # noqa: E402
import cairo                                                      # noqa: E402
import gi                                                         # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GdkPixbuf                          # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        FAILED.append(name)
    print(("PASS " if ok else "FAIL ") + name +
          (("  -- " + str(detail)) if (detail and not ok) else ""))
    return ok


def guarded(name):
    """Run a check body; anything raised inside it fails THIS check by name."""
    def wrap(fn):
        try:
            fn()
        except Exception as exc:                                  # noqa: BLE001
            check(name, False, "%s: %s" % (type(exc).__name__, exc))
        return fn
    return wrap


# ---------------------------------------------------------------- a machine
def _write(path, text, mode=None):
    try:
        os.chmod(path, 0o600)      # a previous check may have made it 000
    except OSError:
        pass
    with open(path, "w") as fh:
        fh.write(text)
    if mode is not None:
        os.chmod(path, mode)
    return path


def fake_machine(pretty=None, shadow_mode=None):
    """Scratch /etc/passwd, /etc/shadow and /etc/notebookos-user, so this runs
    against an INSTALLED machine rather than the developer's host."""
    d = os.path.join(WORK, "etc")
    os.makedirs(d, exist_ok=True)
    passwd = _write(os.path.join(d, "passwd"),
                    "root:x:0:0:root:/root:/bin/sh\n"
                    "ben:x:1000:1000:ben:/home/ben:/bin/sh\n")
    shadow = _write(os.path.join(d, "shadow"),
                    "root:*:19000:0:99999:7:::\n"
                    "ben:$6$abcdefghijklmnop$3Ib1Q0/3Kd3.KZ0kkPtjy0YQ4/"
                    "yQ0zqf1lZQ0h.9j5.z7YyDVvvvvvvvvvvvvvvvvvvvvvvvvvvvvv"
                    "vvvvvvvvvvvv0:19000:0:99999:7:::\n",
                    mode=shadow_mode)
    name = os.path.join(d, "username")
    if pretty is None:
        if os.path.exists(name):
            os.remove(name)
    else:
        _write(name, pretty)
    return passwd, shadow, name


def screen(lock=False, pretty=None, shadow_mode=None, size=(1024, 740),
           tag="s"):
    """The real Login window, hosted offscreen at `size`. The file constants
    are pointed at the scratch machine BEFORE the window is built."""
    import importlib
    d = appdrive.Drive.__new__(appdrive.Drive)
    d.w, d.h = size
    d.home = os.path.join(os.environ["NB_DRIVE_HOME_ROOT"], tag)
    d.nbapp = appdrive._prep_home(d.home)
    uishot.load_theme()
    import nbmotion
    nbmotion.policy = lambda duration=0, fade=False: 0
    d.nbapp.screen_size = lambda: (d.w, d.h)
    sys.modules.pop("login", None)
    login = importlib.import_module("login")
    if login.nbkeyboard is not None:
        # never touch the developer's own X keyboard
        login.nbkeyboard.apply = lambda code, timeout=10: True
        login.nbkeyboard.live_code = lambda timeout=3: ""
    p, s, u = fake_machine(pretty=pretty, shadow_mode=shadow_mode)
    login.PASSWD, login.SHADOW, login.USER_NAME_FILE = p, s, u
    d.mod = login
    d.app = login.Login(lock=lock)
    d._host()
    return d, login


def wrong(d, text="nope"):
    """One wrong password, typed and submitted the way a person does."""
    d.app.entry.grab_focus()
    d.type(text)
    d.key("Return")
    d.pump(0.3)


def crop_mean_diff(a, b, x, y, w, h):
    """Mean per-channel difference of one rectangle of two PNGs."""
    pa = GdkPixbuf.Pixbuf.new_from_file(a).new_subpixbuf(x, y, w, h)
    pb = GdkPixbuf.Pixbuf.new_from_file(b).new_subpixbuf(x, y, w, h)
    da = pa.read_pixel_bytes().get_data()
    db = pb.read_pixel_bytes().get_data()
    n = min(len(da), len(db))
    return sum(abs(da[i] - db[i]) for i in range(n)) / float(n or 1)


print("== the password field keeps what was typed ==")
d, login = screen(tag="caret")
wrong(d)                       # the tick only appears after a failure


@guarded("show password keeps the caret and what is in the field")
def _show_tick():
    d.app.entry.grab_focus()
    d.type("secret")
    d.app._show.grab_focus()   # a real click focuses the box first
    d.app._show.clicked()
    d.pump(0.1)
    d.type("X")
    check("show password keeps the caret and what is in the field",
          d.app.entry.get_text() == "secretX",
          "typed secret, ticked Show password, typed X -> %r"
          % d.app.entry.get_text())


@guarded("unticking show password keeps what is in the field")
def _show_untick():
    d.app.entry.set_text("secret")
    d.app.entry.grab_focus()
    d.app.entry.set_position(-1)
    d.app._show.grab_focus()
    d.app._show.clicked()
    d.pump(0.1)
    d.type("Y")
    check("unticking show password keeps what is in the field",
          d.app.entry.get_text() == "secretY",
          "untick then type Y -> %r" % d.app.entry.get_text())


print("\n== the date reads the way the rest of the OS writes it ==")
DATE_PROBE = r'''
import sys, time
sys.path.insert(0, %r)
import login, nbi18n


class L:
    def __init__(self):
        self.text = ""

    def set_text(self, s):
        self.text = s


w = login.Login.__new__(login.Login)
w._closed = False
w._clock_id = 0
w.clock, w.date = L(), L()
w._tick_clock()
now = time.localtime()
whole = "%%s %%d %%s" %% (time.strftime("%%A", now), now.tm_mday,
                          time.strftime("%%B", now))
print(w.date.text)
print(nbi18n._t(whole))
''' % (DE,)


@guarded("the date under the clock reads in the language's own order")
def _date_order():
    bad = []
    for lang in ("ja", "zh", "ko", "ru", "es"):
        env = dict(os.environ, NB_LANG=lang)
        out = subprocess.run([sys.executable, "-c", DATE_PROBE], env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             timeout=120).stdout.decode("utf-8").splitlines()
        if len(out) < 2:
            bad.append("%s: no answer" % lang)
            continue
        mine, panel = out[0].strip(), out[1].strip()
        if mine != panel:
            bad.append("%s: login %r vs the panel's %r" % (lang, mine, panel))
    check("the date under the clock reads in the language's own order",
          not bad, "; ".join(bad))


print("\n== nothing above the field moves ==")
m, mlogin = screen(tag="move")


@guarded("the clock and the field stay put when the screen answers")
def _no_move():
    def y():
        return (m.app.clock.get_allocation().y,
                m.app.entry.get_allocation().y,
                m.app.entry.get_allocation().width)
    at_launch = y()
    wrong(m)
    after_fail = y()
    m.app.entry.grab_focus()
    m.type("a")                # the error line hides again
    m.pump(0.2)
    after_key = y()
    check("the clock and the field stay put when the screen answers",
          at_launch == after_fail == after_key,
          "launch %r, after a wrong password %r, after the next key %r"
          % (at_launch, after_fail, after_key))


@guarded("the password field keeps one width")
def _one_width():
    wide, wlogin = screen(pretty="Maximilian Bartholomew Fitzgerald-"
                                 "Worthington III", tag="wide")
    plain = m.app.entry.get_allocation().width
    beside_name = wide.app.entry.get_allocation().width
    wrong(wide)
    after = wide.app.entry.get_allocation().width
    check("the password field keeps one width",
          plain == beside_name == after,
          "on its own %d, beside a long name %d, after a failure %d"
          % (plain, beside_name, after))
    check("a long name ends in an ellipsis rather than mid-word",
          wlogin.display_name(wide.app.user).startswith("Maximilian Bartholomew"
                                                        " Fitzgerald-Worthington")
          and [w for w in wide.walk() if isinstance(w, Gtk.Label)
               and w.get_text().startswith("Maximilian")
               and w.get_layout().is_ellipsized()],
          "display_name gave %r" % wlogin.display_name(wide.app.user))
    wide.close()


print("\n== the pause after three failures ==")
p, plogin = screen(tag="pause")


@guarded("a field that has stopped accepting keys says so")
def _pause_says():
    wrong(p)
    wrong(p)
    wrong(p)
    held = p.app.entry.get_sensitive()
    said = p.app._wait.get_visible() and p.app._wait.get_text().strip()
    first = p.app._wait.get_text()
    check("a field that has stopped accepting keys says so",
          (not held) and said,
          "held=%s, the line on screen is %r" % (not held, first))
    p.pump(1.4)
    counted = p.app._wait.get_text()
    check("the pause counts down while it runs",
          counted != first and counted.strip(),
          "%r then %r" % (first, counted))
    p.pump(4.0)
    check("the pause releases and takes its line with it",
          p.app.entry.get_sensitive() and not p.app._wait.get_visible(),
          "sensitive=%s line still up=%s" % (p.app.entry.get_sensitive(),
                                             p.app._wait.get_visible()))


@guarded("a held field does not look like a live one")
def _pause_looks():
    q, qlogin = screen(tag="paint")
    wrong(q)
    wrong(q)
    wrong(q)
    a = q.app.entry.get_allocation()
    held = q.shot(os.path.join(WORK, "held.png"))
    q.app._re_enable()
    q.pump(0.2)
    live = q.shot(os.path.join(WORK, "live.png"))
    diff = crop_mean_diff(held, live, a.x, a.y, a.width, a.height)
    check("a held field does not look like a live one",
          diff > 0.05,
          "mean difference over the field rectangle is %.3f/255" % diff)
    q.close()


print("\n== there is a way to stop the computer ==")


@guarded("both surfaces offer a way to stop the computer")
def _power_there():
    bad = []
    for lock in (False, True):
        s, slogin = screen(lock=lock, tag="pwr-%d" % lock)
        labels = [w.get_label() for w in s.walk()
                  if isinstance(w, Gtk.Button) and w.get_visible()
                  and w.get_sensitive() and w.get_label()]
        if len(labels) < 3:      # sign in / unlock, and two ways out
            bad.append("%s: %r" % ("lock" if lock else "sign-in", labels))
        s.close()
    check("both surfaces offer a way to stop the computer", not bad,
          "; ".join(bad))


@guarded("stopping the computer is asked about first")
def _power_asks():
    s, slogin = screen(lock=True, tag="ask")
    fired = []
    slogin.Login._do_power = staticmethod(lambda mode: fired.append(mode))
    s.app.entry.grab_focus()
    s.type("half")
    stop = [w for w in s.app._pwr_row.get_children()
            if isinstance(w, Gtk.Button)][-1]
    stop.clicked()
    s.pump(0.2)
    check("stopping the computer is asked about first",
          not fired and s.app._pwr_ask.get_visible()
          and s.app._pwr_q.get_text().strip(),
          "fired=%r question=%r" % (fired, s.app._pwr_q.get_text()))
    s.key("Escape")
    s.pump(0.1)
    s.type("X")
    check("backing out of that question keeps the password typed so far",
          not fired and s.app.entry.get_text() == "halfX"
          and s.app._pwr_row.get_visible(),
          "fired=%r field=%r" % (fired, s.app.entry.get_text()))
    stop.clicked()
    s.pump(0.1)
    s.app._pwr_yes.clicked()
    s.pump(0.1)
    check("the confirmation is what actually stops the computer",
          fired == ["poweroff"], "fired=%r" % (fired,))
    s.close()


def _escape():
    from gi.repository import Gdk
    ev = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    ev.keyval = Gdk.KEY_Escape
    return ev


@guarded("a screen that cannot check a password is not a dead end")
def _dead_end():
    s, slogin = screen(lock=True, shadow_mode=0o000, tag="unreadable")
    check("a lock screen is still shown when the credential file is unreadable",
          slogin._may_skip_login(s.app.user, lock=True) is False
          and slogin._may_skip_login(s.app.user, lock=False) is True,
          "state=%r" % slogin._password_state(s.app.user))
    s.app.state_unverifiable()
    s.pump(0.2)
    ways = [w.get_label() for w in s.walk()
            if isinstance(w, Gtk.Button) and w.get_visible()
            and w.get_sensitive() and w.get_label()]
    check("a screen that cannot check a password is not a dead end",
          (not s.app.entry.get_sensitive()) and (not s.app._go.get_sensitive())
          and s.app.error.get_visible()
          and s.app.error.get_text().strip() and len(ways) >= 2,
          "error=%r, what is left to press: %r"
          % (s.app.error.get_text(), ways))
    check("Escape still does not dismiss the screen",
          s.app._on_key(s.app, _escape()) is True, "")
    s.close()


def _rendered_lines(lbl):
    """The lines a label actually draws, in order."""
    lay = lbl.get_layout()
    raw = lbl.get_text().encode("utf-8")
    out = []
    for i in range(lay.get_line_count()):
        ln = lay.get_line(i)
        out.append(raw[ln.start_index:ln.start_index + ln.length]
                   .decode("utf-8", "replace"))
    return out


print("\n== what the screen says back ==")


@guarded("the two answers after a wrong password are two paragraphs")
def _paragraphs():
    s, slogin = screen(tag="answers")
    if slogin.nbkeyboard is not None:
        # a dual-layout machine (ru,us): the alphabet sentence is the one
        # thing on this screen that can be acted on, and it is a different
        # answer from the reminder underneath it
        s.app._kb_groups = [("ru", ""), (slogin.nbkeyboard.LATIN_FALLBACK, "")]
        s.app._kb_active = 0
    wrong(s)
    kbd, recall = s.app._kbdnote, s.app._recall
    ka, ra = kbd.get_allocation(), recall.get_allocation()
    gap = ra.y - (ka.y + ka.height)
    check("the two answers after a wrong password are two paragraphs",
          kbd.get_visible() and recall.get_visible()
          and kbd is not recall and gap >= 6,
          "keyboard sentence %r, recall %r, %dpx between them"
          % (kbd.get_text()[:40], recall.get_text()[:40], gap))
    bad = []
    for lbl in (kbd, recall):
        # the RENDERED lines, off the label's own Pango layout: reading
        # get_text() would only ever see the breaks this screen put in, so a
        # label left to wrap greedily would sail through with one "line"
        lines = [ln for ln in _rendered_lines(lbl) if ln.strip()]
        if len(lines) > 1 and len(lines[-1].split()) < 2:
            bad.append(lines[-1])
    check("no answer ends with a single word alone on a line",
          not bad, "orphans: %r" % (bad,))
    s.close()


print("")
if FAILED:
    print("LOGIN REAL-USE SELFTEST: %d FAILED: %s"
          % (len(FAILED), ", ".join(FAILED)))
else:
    print("LOGIN REAL-USE SELFTEST: all checks passed")
    # ...in the runner's own grammar. "all checks passed" is prose, and a suite
    # that dies half way prints its PASS lines too, so run_all_gates reads a
    # bare zero exit as DID NOT RUN (SUCCESSWORD).
    print("RESULT: ALL PASS")
sys.exit(len(FAILED))
