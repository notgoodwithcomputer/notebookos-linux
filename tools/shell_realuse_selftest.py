#!/usr/bin/env python3
"""shell_realuse_selftest — the desktop panel driven the way a person uses it.

    tools/guestrun.sh python3 tools/shell_realuse_selftest.py

Every check here builds the REAL Panel — its own widget tree, its own handlers,
its own GLib main loop, through tools/appdrive — because each defect it pins was
invisible to a check that called a method by hand on a stand-in object. The
plainest case is the fullscreen-video poll: _poll_video_full was correct and
simply never scheduled, while a green suite proved it worked by invoking it
unbound on a SimpleNamespace. So: the flag goes on disk, the panel's own main
loop runs for two seconds, and the bar either got out of the way or it did not.

The process runs in GERMAN. Four of these are language defects and English
cannot show them — a date copied to the clipboard, the battery's hover text, a
system message that has to fit, a failure sentence. $NB_LANG is read once, when
nbi18n is imported, so it is set here before anything from de/ is loaded.
"""
import os
import sys
import json
import time
import shutil
import builtins
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, os.path.join(ROOT, "tools"))

os.environ["NB_LANG"] = "de"
HOME = tempfile.mkdtemp(prefix="shell-realuse-")
os.environ["NB_HOME"] = HOME

import appdrive                                             # noqa: E402
import gi                                                   # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib                    # noqa: E402

SCREEN_W, SCREEN_H = 1024, 768

# ---------------------------------------------------------------- reporting
_REPORTED = set()
_FAILED = []


def check(name, ok, detail=""):
    _REPORTED.add(name)
    if ok:
        print("PASS", name)
    else:
        _FAILED.append(name)
        print("FAIL", name, ("— " + detail) if detail else "")


def group(names, fn):
    """Run one group of checks. A crash inside it becomes a named FAIL for
    every check the group did not get to report — a check must fail by name,
    never by traceback."""
    try:
        fn()
    except Exception:                                       # noqa: BLE001
        traceback.print_exc()
    for name in names:
        if name not in _REPORTED:
            check(name, False, "the drive did not reach this check")


# ------------------------------------------------------------------- set-up
def fake_screen(w, h):
    """Make the Panel believe the primary monitor is w x h: it reads the Gdk
    monitor geometry directly in __init__, so without this every dropdown is
    placed for the developer's screen."""
    rect = Gdk.Rectangle()
    rect.x = rect.y = 0
    rect.width, rect.height = w, h
    Gdk.Monitor.get_geometry = lambda self: rect


# A real gtk_window_show is what hands out a window's INITIAL FOCUS, which is
# the mechanism behind the About card opening with its kernel string selected —
# so these checks need the window really shown, not a child-only show_all. A
# selftest must still not throw windows in front of whoever is running it, so
# every toplevel is parked off the visible screen first. The original here is
# nbi18n's patched show_all: the translation walk must keep running.
_REAL_SHOW_ALL = Gtk.Widget.show_all


def _offscreen_show_all(self):
    try:
        self.move(-4000, -4000)
        # ...and it never takes the keyboard from whatever else is running on
        # this display. gtk_window_show still hands out the window's INTERNAL
        # focus (the mechanism these checks are about), but the X focus stays
        # where it was, so a keystroke meant for another window on a shared
        # :0 cannot reach a modal card here and close it mid-check.
        self.set_accept_focus(False)
    except Exception:                                       # noqa: BLE001
        pass
    return _REAL_SHOW_ALL(self)


Gtk.Window.show_all = _offscreen_show_all

fake_screen(SCREEN_W, SCREEN_H)
d = appdrive.Drive("shell", cls="Panel", size=(SCREEN_W, SCREEN_H), home=HOME)
app = d.app
shell = d.mod
# A private flag path, so driving the video poll can never disturb a real
# session's Media Viewer (or be disturbed by one).
shell.VIDEO_FULL_FLAG = os.path.join(HOME, "nb-video-fullscreen")

import nbnotify                                             # noqa: E402
import nbi18n                                               # noqa: E402
from nbi18n import _t                                       # noqa: E402


def widgets(root):
    out, stack = [], [root]
    while stack:
        w = stack.pop(0)
        out.append(w)
        if isinstance(w, Gtk.Container):
            stack.extend(w.get_children())
    return out


def has_class(w, name):
    return w.get_style_context().has_class(name)


def bar_button(text):
    for w in widgets(d.child):
        if isinstance(w, Gtk.Button) and has_class(w, "menuitem"):
            ch = w.get_child()
            if isinstance(ch, Gtk.Label) and ch.get_text() == text:
                return w
    raise LookupError("no bar button %r" % text)


def menu_row(prefix):
    for w in widgets(app._menu):
        if not isinstance(w, Gtk.Button):
            continue
        text = " ".join(x.get_text() for x in widgets(w)
                        if isinstance(x, Gtk.Label))
        if text.startswith(prefix):
            return w
    raise LookupError("no menu row %r" % prefix)


def open_tray():
    if app._menu is not None:
        app._menu_close()
    app.bell.clicked()
    d.pump(0.3)
    return app._menu


def toplevels():
    return [w for w in Gtk.Window.list_toplevels()
            if w.get_visible() and w is not app
            and not isinstance(w, Gtk.OffscreenWindow)]


def toplevel_holding(kind, pick=lambda _w: True):
    """The visible window that holds a widget of `kind` the check is about.

    Not toplevels()[-1]: gtk_window_list_toplevels() is not in creation order,
    so on a run where any other window is still up (a card being torn down,
    a tooltip) the last entry was some OTHER window — and a check that then
    read an empty widget list failed for a reason that had nothing to do with
    the panel. Naming what the window must CONTAIN makes the pick exact."""
    for w in toplevels():
        for x in widgets(w):
            if isinstance(x, kind) and pick(x):
                return w
    return None


def close_toplevels():
    for w in toplevels():
        w.destroy()
    d.pump(0.2)


def spool_titles():
    return [rec.get("title") for rec in nbnotify.load()]


# The catalog has to be the German one or four of these checks would compare a
# string with itself and pass on an empty catalog.
GERMAN = "the German catalog is loaded (the language checks below need it)"
check(GERMAN,
      bool(nbi18n._CAT) and _t("Battery") != "Battery"
      and _t("Discharging") != "Discharging",
      "NB_LANG=de did not load lang_de.json")


# ------------------------------------------------- 1/2: fullscreen video
VIDEO_LIVE = "a live fullscreen-video flag gets the bar out of the way"
VIDEO_STALE = "a stale fullscreen-video flag is cleared by the running panel"


def _process_token(pid):
    with open("/proc/%s/stat" % pid) as fh:
        tail = fh.read().rsplit(") ", 1)[1].split()
    return "%s %s" % (pid, tail[19])


def video_checks():
    seen = []
    state = {"visible": True}
    app.get_visible = lambda: state["visible"]

    def hide():
        state["visible"] = False
        seen.append("hide")

    def show():
        state["visible"] = True
        seen.append("show")

    app.hide, app.show = hide, show
    app._reserve_strut = lambda *_a: None
    app._apply_shape = lambda *_a: None
    try:
        # a player that really is running, written exactly as media.py writes it
        with open(shell.VIDEO_FULL_FLAG, "w") as fh:
            fh.write(_process_token(os.getpid()))
        d.pump(2.5)                     # the panel's OWN main loop, nothing else
        check(VIDEO_LIVE, "hide" in seen and not state["visible"],
              "after 2.5s of the main loop the bar was still painted over the "
              "picture (hide calls: %r)" % seen)
        # ...and a flag whose owner has gone must not strand the desktop
        with open(shell.VIDEO_FULL_FLAG, "w") as fh:
            fh.write("999999 1")
        d.pump(2.5)
        check(VIDEO_STALE,
              not os.path.exists(shell.VIDEO_FULL_FLAG) and state["visible"],
              "the stale flag is still on disk and the bar is still hidden")
    finally:
        for attr in ("get_visible", "hide", "show",
                     "_reserve_strut", "_apply_shape"):
            try:
                delattr(app, attr)
            except AttributeError:
                pass
        if os.path.exists(shell.VIDEO_FULL_FLAG):
            os.remove(shell.VIDEO_FULL_FLAG)


group([VIDEO_LIVE, VIDEO_STALE], video_checks)


# --------------------------------------------------- 3: Copy Date & Time
COPY_LANG = "Copy Date & Time copies the date in the interface language"


def copy_checks():
    copied = []
    app._copy_text = lambda text: copied.append(text)
    try:
        bar_button(_t("Edit")).clicked()
        d.pump(0.2)
        menu_row(_t("Copy Date & Time")).clicked()
        d.pump(0.2)
    finally:
        del app._copy_text
    bar_weekday = app.datelbl.get_text().split()[0]     # "Mo"
    english_weekday = time.strftime("%a")               # "Mon"
    got = copied[-1] if copied else ""
    check(COPY_LANG,
          bar_weekday != english_weekday and bool(got)
          and bar_weekday in got and english_weekday not in got,
          "the bar says %r and the clipboard got %r" % (bar_weekday, got))


group([COPY_LANG], copy_checks)


# ------------------------------- 4/5/6: the Show Clipboard card
CARD_VERBATIM = "the clipboard card shows the clipboard's own text, unchanged"
CARD_GROWS = "the clipboard card grows with its body instead of a fixed box"
CARD_WIDTH = "an unbroken paste cannot widen the clipboard card"


class _StubClipboard:
    """An X clipboard owner that answers at once. The real one belongs to the
    developer's session and a selftest has no business overwriting it."""

    def __init__(self, text):
        self.text = text

    def request_text(self, callback, _data=None):
        callback(self, self.text, None)

    def set_text(self, *_a):
        pass

    def store(self):
        pass


def show_clipboard(text):
    """Drive the real _show_clipboard for `text`; return (texts, w, h) of the
    card it builds, without ever mapping it."""
    got = {}
    real_run, real_show = Gtk.Dialog.run, Gtk.Dialog.show_all

    def trap_show(dlg):
        child = dlg.get_child()
        if child is not None:
            child.show_all()            # nbi18n's walk still runs on the card
            got["texts"] = [w.get_text() for w in widgets(child)
                            if isinstance(w, Gtk.Label)]
            _min, nat = child.get_preferred_size()
            got["size"] = (nat.width, nat.height)

    def trap_run(dlg):
        trap_show(dlg)
        return Gtk.ResponseType.CANCEL

    Gtk.Dialog.show_all, Gtk.Dialog.run = trap_show, trap_run
    app._clipboard = lambda: _StubClipboard(text)
    try:
        app._show_clipboard()
        d.pump(0.3)
    finally:
        Gtk.Dialog.show_all, Gtk.Dialog.run = real_show, real_run
        del app._clipboard
    return got.get("texts", []), got.get("size", (0, 0))


def clipboard_card_checks():
    # a word that IS a catalog key: the card must still report what was copied
    texts, one_line = show_clipboard("Save")
    check(CARD_VERBATIM,
          _t("Save") != "Save" and "Save" in texts and _t("Save") not in texts,
          "the clipboard held 'Save' and the card says %r" % (texts,))

    _texts, five_lines = show_clipboard("one\ntwo\nthree\nfour\nfive")
    check(CARD_GROWS,
          one_line[1] < five_lines[1] and one_line[1] < 300,
          "one line and five lines both make a %dpx card" % one_line[1])

    _texts, huge = show_clipboard("x" * 4000)
    check(CARD_WIDTH, huge[0] <= 480,
          "a 4000-character paste makes the card %dpx wide" % huge[0])


group([CARD_VERBATIM, CARD_GROWS, CARD_WIDTH], clipboard_card_checks)


# ------------------------------------------------------------- 7: About
ABOUT_SEL = "About This Notebook opens with nothing selected"


def about_checks():
    app._about()
    d.pump(0.5)
    try:
        win = toplevel_holding(Gtk.Label, lambda x: x.get_selectable())
        labels = [] if win is None else [
            w for w in widgets(win.get_child())
            if isinstance(w, Gtk.Label) and w.get_selectable()]
        selected = [w.get_text() for w in labels
                    if w.get_selection_bounds()[0]]
        focus = win.get_focus() if win is not None else None
        check(ABOUT_SEL,
              bool(labels) and not selected
              and isinstance(focus, Gtk.Button),
              "opened with %r highlighted (focus: %r; windows on screen: %r)"
              % (selected, focus, [w.get_title() for w in toplevels()]))
    finally:
        close_toplevels()


group([ABOUT_SEL], about_checks)


# ------------------------------------------------------------ 8: Labels
LABELS_RETURN = "Return saves in the Labels dialog"


def labels_checks():
    if os.path.exists(shell.SHELL_FILE):
        os.remove(shell.SHELL_FILE)
    app._label_names = ["", "", "", "", "", ""]
    app._edit_labels()
    d.pump(0.4)
    try:
        win = toplevel_holding(Gtk.Entry)
        entries = [] if win is None else [
            w for w in widgets(win.get_child())
            if isinstance(w, Gtk.Entry)]
        if not entries:
            check(LABELS_RETURN, False, "no Labels window among %r"
                  % [w.get_title() for w in toplevels()])
            return
        entries[0].set_text("Dringend")
        # what a Return in an entry with set_activates_default does, and all
        # gtk_entry_real_activate ever does: fire the window's default widget.
        win.activate_default()
        d.pump(0.3)
        saved = {}
        if os.path.exists(shell.SHELL_FILE):
            with open(shell.SHELL_FILE) as fh:
                saved = json.load(fh)
        check(LABELS_RETURN,
              all(e.get_activates_default() for e in entries)
              and "Dringend" in saved.get("label_names", []),
              "default widget %r, shell.json %r (windows on screen: %r)"
              % (win.get_default_widget(), saved,
                 [w.get_title() for w in toplevels()]))
    finally:
        close_toplevels()


group([LABELS_RETURN], labels_checks)


# ------------------------------------------------- 9: click-away on the bar
BAR_DISMISS = "a click on the bar background dismisses an open dropdown"


def press_panel(x, y):
    ev = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
    ev.window = app.get_window()
    ev.x, ev.y = float(x), float(y)
    ev.button = 1
    ev.state = Gdk.ModifierType(0)
    ev.time = 0
    app.emit("button-press-event", ev)
    d.pump(0.1)


def dismiss_checks():
    bar_button(_t("View")).clicked()
    d.pump(0.2)
    was_open = app._menu is not None
    rect = app._menu_rect
    press_panel(600, 20)                       # bar background, no title there
    closed_by_bar = app._menu is None
    bar_button(_t("View")).clicked()           # ...and a click INSIDE stays put
    d.pump(0.2)
    press_panel(rect[0] + 5, rect[1] + 5)
    still_open = app._menu is not None
    app._menu_close()
    check(BAR_DISMISS, was_open and closed_by_bar and still_open,
          "open=%s closed by a bar click=%s survived a click inside=%s"
          % (was_open, closed_by_bar, still_open))


group([BAR_DISMISS], dismiss_checks)


# ------------------------------------------- 10/11: what the panel posts
REFUSAL_ROW = "a refused app is named in the tray and is not offered for opening"
SYSTEM_SENDER = "a panel save failure names its sender"


def refusal_checks():
    nbnotify.clear_all()
    real_check = shell.nbtrust.check_path if shell.nbtrust else None
    if shell.nbtrust is not None:
        # force the REFUSAL branch rather than depending on what this tree's
        # signatures happen to say — and so nothing is ever really launched.
        shell.nbtrust.check_path = lambda _p: (False, "the test says no")
    try:
        shell.launch("settings", _t("Settings"))
        d.pump(0.3)
    finally:
        if shell.nbtrust is not None:
            shell.nbtrust.check_path = real_check
    records = nbnotify.load()
    rec = records[0] if records else {}
    named = (rec.get("app_name") == _t("Settings") and not rec.get("app")
             and bool(rec.get("icon")))
    # ...and the row it makes only dismisses: no promise to open what was
    # just refused, and no second refusal posted behind the first.
    open_tray()
    rows = [w for w in widgets(app._menu)
            if isinstance(w, Gtk.Button) and has_class(w, "nbn-row")]
    tooltip = rows[0].get_tooltip_text() if rows else ""
    rows[0].clicked()
    d.pump(0.5)
    left = nbnotify.load()
    check(REFUSAL_ROW,
          named and tooltip == _t("Dismiss") and not left,
          "record %r, row tooltip %r, tray after the click %r"
          % (rec, tooltip, spool_titles()))


def save_failure_checks():
    nbnotify.clear_all()
    os.makedirs(shell.CFG_DIR, exist_ok=True)
    os.chmod(shell.CFG_DIR, 0o555)
    try:
        wrote = app._persist("clock_24h", True)
    finally:
        os.chmod(shell.CFG_DIR, 0o755)
    d.pump(0.2)
    records = nbnotify.load()
    rec = records[0] if records else {}
    check(SYSTEM_SENDER,
          wrote is False and rec.get("app_name") == _t("System")
          and bool(rec.get("title")),
          "_persist returned %r and posted %r" % (wrote, rec))


group([REFUSAL_ROW], refusal_checks)
group([SYSTEM_SENDER], save_failure_checks)


# ------------------------------------------------- 12: a readable message
TITLE_FITS = "a system message in the tray can be read in full"


def title_checks():
    nbnotify.clear_all()
    title = _t("This app can't be opened on this computer.")
    nbnotify.post(title, app_name=_t("Settings"))
    d.pump(0.2)
    open_tray()
    labels = [w for w in widgets(app._menu)
              if isinstance(w, Gtk.Label) and has_class(w, "nbn-msg")]
    label = labels[0] if labels else None
    cut = label.get_layout().is_ellipsized() if label is not None else True
    check(TITLE_FITS,
          len(title) > 34 and label is not None and not cut
          and label.get_lines() == 2,
          "%r (%d chars) is cut off in the row" % (title, len(title)))
    app._menu_close()


group([TITLE_FITS], title_checks)


# --------------------------------------------- 13/14: a long tray's shape
TRAY_MARGIN = "a full notification tray stops short of the bottom of the screen"
TRAY_HEAD = "the tray heading stays put while the list scrolls"


def long_tray_checks():
    nbnotify.clear_all()
    for i in range(40):
        nbnotify.post("Nachricht %d" % i, body="Fertig.", app="media",
                      app_name="Media Viewer")
    d.pump(0.3)
    open_tray()
    x, y, w, h = app._menu_rect
    check(TRAY_MARGIN, y + h <= SCREEN_H - 20,
          "the card runs to y=%d on a %dpx screen" % (y + h, SCREEN_H))

    head = [wd for wd in widgets(app._menu)
            if isinstance(wd, Gtk.Box) and has_class(wd, "nbn-head")]
    scroller = [wd for wd in widgets(app._menu)
                if isinstance(wd, Gtk.ScrolledWindow)]
    inside = False
    node = head[0].get_parent() if head else None
    while node is not None and node is not app._menu:
        if isinstance(node, Gtk.ScrolledWindow):
            inside = True
        node = node.get_parent()
    moved = None
    if scroller and head:
        adj = scroller[0].get_vadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())
        d.pump(0.3)
        moved = head[0].translate_coordinates(app._menu, 0, 0)
    check(TRAY_HEAD,
          bool(head) and bool(scroller) and not inside
          and moved is not None and moved[1] >= 0,
          "heading inside the scroller=%s, at y=%r once scrolled to the end"
          % (inside, moved))
    app._menu_close()


group([TRAY_MARGIN, TRAY_HEAD], long_tray_checks)


# ------------------------------------------------ 15: a failed Clear All
CLEAR_FAIL = "a failed Clear All says what happened, in the alert register"


def clear_fail_checks():
    nbnotify.clear_all()
    for i in range(3):
        nbnotify.post("Nachricht %d" % i, app="media", app_name="Media Viewer")
    d.pump(0.2)
    os.chmod(nbnotify.SPOOL, 0o555)
    try:
        open_tray()
        clear = [w for w in widgets(app._menu)
                 if isinstance(w, Gtk.Button) and has_class(w, "nbn-clear")]
        clear[0].clicked()
        d.pump(0.4)
        said = app._notify_error
        line = [w for w in widgets(app._menu)
                if isinstance(w, Gtk.Label) and has_class(w, "warn")]
        colour = None
        if line:
            ctx = line[0].get_style_context()
            colour = ctx.get_color(ctx.get_state())
        alert = colour is not None and (round(colour.red * 255),
                                        round(colour.green * 255),
                                        round(colour.blue * 255)) == (200, 52, 30)
        check(CLEAR_FAIL,
              said == _t("%d item%s could not be deleted.") % (3, "s")
              and _t("Notifications") not in said and alert,
              "the card says %r, drawn in %r" % (said, colour))
    finally:
        os.chmod(nbnotify.SPOOL, 0o755)
        nbnotify.clear_all()
        app._menu_close()


group([CLEAR_FAIL], clear_fail_checks)


# --------------------------------------------------- 16: battery hover text
BATTERY_LANG = "the battery hover text is in the interface language"


def battery_checks():
    base = "/sys/class/power_supply"
    fake = os.path.join(HOME, "fake-power")
    shutil.rmtree(fake, ignore_errors=True)
    os.makedirs(os.path.join(fake, "BAT0"))
    for key, value in (("type", "Battery"), ("status", "Discharging"),
                       ("capacity", "7")):
        with open(os.path.join(fake, "BAT0", key), "w") as fh:
            fh.write(value + "\n")

    real_listdir, real_open = os.listdir, builtins.open

    def redirect(path):
        if isinstance(path, str) and path.startswith(base):
            return fake + path[len(base):]
        return path

    # only /sys/class/power_supply is redirected, so shell.py's real
    # _battery_pct runs over the fake tree unmodified
    os.listdir = lambda p=".": real_listdir(redirect(p))
    builtins.open = lambda f, *a, **kw: real_open(redirect(f), *a, **kw)
    try:
        app._tick()
        d.pump(0.2)
        tip = app.batlbl.get_tooltip_text() or ""
    finally:
        os.listdir, builtins.open = real_listdir, real_open
        shutil.rmtree(fake, ignore_errors=True)
    check(BATTERY_LANG,
          _t("Battery") in tip and _t("Discharging") in tip
          and "Discharging" not in tip and "Battery " not in tip,
          "the bar's battery reads %r beside a clock that reads %r"
          % (tip, app.clocklbl.get_tooltip_text()))


group([BATTERY_LANG], battery_checks)


# ------------------------------------------------------------------ result
d.close()
shutil.rmtree(HOME, ignore_errors=True)
if _FAILED:
    print("RESULT: FAIL (%d) — %s" % (len(_FAILED), "; ".join(_FAILED)))
    sys.exit(1)
print("RESULT: ALL PASS")
