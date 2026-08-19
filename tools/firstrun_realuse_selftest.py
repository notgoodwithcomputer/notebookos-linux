#!/usr/bin/env python3
"""First-run setup, driven the way its owner meets it -- on real pixels.

WHY THIS SUITE IS NOT DISPLAY-FREE. The other two firstrun suites test what
apply() writes, which is the half of this screen that can be answered without
GTK. Everything below is about what the screen SAYS while somebody is standing
in front of it, and four of the five defects it pins were invisible to state:
the widget was insensitive and looked live, the widget was cleared and the
screen said nothing, the field held 53 characters and the disk kept 40, the
tick box sat flush against its own caption in the one right-to-left language.
So the window is built for real, hosted offscreen at the smallest supported
panel (1024x740) by tools/appdrive.py, and the checks read text, state and --
where the defect is a painted one -- pixels.

Every check is named, and fails by name: an exception inside one is reported as
that check failing, never as a crash that takes the rest of the suite with it.

Run:  tools/guestrun.sh python3 tools/firstrun_realuse_selftest.py
      (a second copy of this file re-runs itself under NB_LANG=yi for the
       right-to-left check, because interface direction is decided at import)
"""
import os
import subprocess as real_subprocess
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, HERE)
sys.path.insert(0, DE)

RTL_MODE = "--rtl" in sys.argv
WORK = os.path.join(os.environ.get("NB_DRIVE_HOME_ROOT", "/tmp"),
                    "nb-firstrun-realuse" + ("-yi" if RTL_MODE else ""))
os.environ["NB_LANG"] = "yi" if RTL_MODE else "en"
os.environ.setdefault("GDK_BACKEND", "x11")

R = []


def chk(name, ok, detail=""):
    R.append(bool(ok))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       "" if ok else "  <- %s" % (detail,)))


def check(name, fn):
    """Run one check. A check that raises FAILS BY NAME -- a suite whose
    verdict depends on an exception reaching the top reports nothing about the
    thing it was asked about."""
    try:
        ok, detail = fn()
    except Exception as exc:                                   # noqa: BLE001
        ok, detail = False, "%s: %s" % (type(exc).__name__, exc)
    chk(name, ok, detail)


# ---------------------------------------------------------------------------
# Scaffolding: a throwaway root, a subprocess that cannot touch this machine,
# and the FirstRun window -- which is defined INSIDE firstrun.main(), so it is
# reached by calling main() with Gtk.main() neutralised.
# ---------------------------------------------------------------------------
class SubShim:
    """`setxkbmap` must not reconfigure the developer's keyboard and
    `hostname` must not rename this machine. Every argv is recorded; the
    return code is 0, which is the guest's normal case."""
    calls = []

    @classmethod
    def run(cls, argv, **kw):
        cls.calls.append(list(argv))
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")


def make_root(tag):
    root = os.path.join(WORK, "root-" + tag)
    real_subprocess.run(["rm", "-rf", root], check=False)
    os.makedirs(os.path.join(root, "etc", "X11", "xorg.conf.d"))
    os.makedirs(os.path.join(root, "var", "lib", "notebookos"))
    shadow = os.path.join(root, "etc", "shadow")
    with open(shadow, "w") as fh:
        fh.write("root:*:19000:0:99999:7:::\n")
    os.chmod(shadow, 0o600)
    open(os.path.join(root, "var", "lib", "notebookos", "first-run"), "w").close()
    return root


def build(tag):
    """-> (drive, firstrun module, fake root). The window is real; only /etc
    and the two binaries it shells out to are stood in for."""
    root = make_root(tag)
    home = os.path.join(WORK, "home-" + tag)
    real_subprocess.run(["rm", "-rf", home], check=False)
    os.makedirs(home)
    import appdrive
    import uishot
    nbapp = appdrive._prep_home(home)
    uishot.load_theme()
    import nbmotion
    nbmotion.policy = lambda duration=0, fade=False: 0
    nbapp.screen_size = lambda: appdrive.PANEL
    sys.modules.pop("firstrun", None)
    import firstrun
    firstrun.OEM_MARKER = os.path.join(root, "var/lib/notebookos/first-run")
    firstrun.HOSTNAME_FILE = os.path.join(root, "etc/hostname")
    firstrun.USER_NAME_FILE = os.path.join(root, "etc/notebookos-user")
    firstrun.SHADOW = os.path.join(root, "etc/shadow")
    firstrun.XKB_CONF = os.path.join(root, "etc/X11/xorg.conf.d/00-keyboard.conf")
    firstrun.subprocess = SubShim
    from gi.repository import Gtk
    before = set(Gtk.Window.list_toplevels())
    quits = []
    saved_main = Gtk.main
    Gtk.main = lambda: None
    # main_quit is NOT put back: the window connects it to "destroy", and
    # closing a drive with no main loop running would print a Gtk-CRITICAL
    # over the suite's own output.
    Gtk.main_quit = lambda *a: quits.append(1)
    try:
        firstrun.main()
    finally:
        Gtk.main = saved_main
    win = [w for w in Gtk.Window.list_toplevels()
           if w not in before and type(w).__name__ == "FirstRun"]
    if not win:
        raise RuntimeError("the first-run window was not built")
    d = appdrive.Drive.__new__(appdrive.Drive)
    d.w, d.h = appdrive.PANEL
    d.home, d.nbapp, d.mod, d.app = home, nbapp, firstrun, win[0]
    d._host()
    d.quits, d.root = quits, root
    return d, firstrun, root


def box_of(d, widget):
    """A widget's rectangle in the shot's own coordinates."""
    x, y = widget.translate_coordinates(d.off.get_child(), 0, 0)
    a = widget.get_allocation()
    return (x, y, x + a.width, y + a.height)


def _png(path):
    import cairo
    surf = cairo.ImageSurface.create_from_png(path)
    return (bytes(surf.get_data()), surf.get_stride(),
            surf.get_width(), surf.get_height())


def differing(a, b, box):
    """How many pixels of `box` differ between two shots, and how many there
    are. Zero, on a region that was supposed to change, is the whole point."""
    da, stride, w, h = _png(a)
    db, _s, _w, _h = _png(b)
    x0, y0, x1, y1 = box
    x1, y1 = min(x1, w), min(y1, h)
    n = diff = 0
    for y in range(y0, y1):
        row = y * stride
        for x in range(x0, x1):
            o = row + x * 4
            n += 1
            if da[o:o + 4] != db[o:o + 4]:
                diff += 1
    return diff, n


def ink_runs(path, box, thresh=24):
    """Runs of columns holding something darker than the paper, left to
    right. The gap between a tick box and its caption is measured, not read
    off the stylesheet -- a physical margin can be present and land on the
    wrong side."""
    data, stride, w, h = _png(path)
    x0, y0, x1, y1 = box
    x1, y1 = min(x1, w), min(y1, h)
    runs, start = [], None
    for x in range(x0, x1):
        dark = False
        for y in range(y0, y1):
            o = y * stride + x * 4
            if ((0xFC - data[o + 2]) > thresh or (0xFB - data[o + 1]) > thresh
                    or (0xF8 - data[o]) > thresh):
                dark = True
                break
        if dark and start is None:
            start = x
        elif not dark and start is not None:
            runs.append((start, x))
            start = None
    if start is not None:
        runs.append((start, x1))
    return runs


os.makedirs(WORK, exist_ok=True)
SHOTS = os.path.join(WORK, "shots")
os.makedirs(SHOTS, exist_ok=True)


# ===========================================================================
# The right-to-left check runs in its own process: nbapp.apply_direction()
# mirrors the whole toolkit at IMPORT, from the language the process started
# in, so "the same process, in Yiddish" does not exist.
# ===========================================================================
def rtl_gap():
    """FR-005 -- the 6px between a tick box and its own caption is written in
    the theme as `margin-right`, a physical side. In Yiddish the caption is on
    the other side of the box, so the gap landed behind the box and the last
    glyph of the caption touched its border."""
    d, fr, root = build("rtl")
    from gi.repository import Gtk
    if Gtk.Widget.get_default_direction() != Gtk.TextDirection.RTL:
        return False, "the toolkit did not mirror: NB_LANG=yi was not honoured"
    shot = d.shot(os.path.join(SHOTS, "yi-form.png"), "yi")
    worst = None
    for name, cb in (("Show password", d.app.cb_show),
                     ("no password", d.app.cb_none)):
        bx = box_of(d, cb)
        runs = ink_runs(shot, bx)
        if not runs:
            return False, "no ink at all on the %r row" % name
        # The box is the 17px block of ink at the reading edge -- the right
        # in RTL -- and the caption is everything before it.
        box_run = [r for r in runs if (r[1] - r[0]) >= 14
                   and r[0] > (bx[0] + bx[2]) // 2]
        if not box_run:
            return False, "could not find the tick box on the %r row" % name
        edge = box_run[-1][0]
        caption = [r for r in runs if r[1] <= edge]
        if not caption:
            return False, "no caption ink beside the box on the %r row" % name
        gap = edge - caption[-1][1]
        if worst is None or gap < worst[0]:
            worst = (gap, name)
    d.close()
    return worst[0] >= 4, "only %dpx between the box and %r" % worst


if RTL_MODE:
    check("a tick box keeps its gap from its caption right-to-left", rtl_gap)
    print("\n%d/%d checks passed" % (sum(1 for x in R if x), len(R)))
    sys.exit(0 if all(R) else 1)


# ===========================================================================
# Everything else, in English, on one window -- the order a person meets it.
# ===========================================================================
# The sentences this suite pins, spelled here rather than imported: they live
# at their call sites in de/firstrun.py, where the text gates can see them, and
# a check that reads them out of the file it is checking cannot notice a change.
CLEARED = "The keyboard changed, so the password was cleared. Type it again."

D, FR, ROOT = build("a")
APP = D.app
from gi.repository import Gtk                                  # noqa: E402
import nbi18n                                                  # noqa: E402


def name_field_says_what_it_is_for():
    """FR-006 -- "NAME" sits directly above "COMPUTER NAME" and an empty
    answer is not an error: it leaves the sign-in screen greeting "root"
    forever, on a screen that never runs again and that nothing else in the OS
    can re-ask. The installer's own field carries these words already."""
    got = APP.e_user.get_placeholder_text() or ""
    return (got == "Shown on the sign-in screen",
            "the NAME field offers nothing but its caption (placeholder %r)"
            % got)


def keyboard_change_says_the_password_went():
    """FR-004 -- clearing the fields is right (anything typed before the
    change came off the old layout), and it happened in silence: the next
    thing the owner met was "Choose a password", which names the wrong
    problem."""
    APP.e_pw.set_text("secret123")
    APP.e_pw2.set_text("secret123")
    D.pump(0.1)
    codes = [c for c, _ in APP._kbds]
    APP.c_kbd.set_active(codes.index("fr"))
    D.pump(0.3)
    said = APP.err.get_text()
    if APP.e_pw.get_text() or APP.e_pw2.get_text():
        return False, "the password fields were not cleared at all"
    if said != CLEARED:
        return False, "the screen said %r" % said
    # ...and it reads as something that happened, not as a complaint.
    classes = APP.err.get_style_context().list_classes()
    if "fr-note" not in classes:
        return False, "the line is styled %s" % (classes,)
    # An untouched form has nothing to report, so it stays quiet.
    APP.c_kbd.set_active(codes.index("us"))
    D.pump(0.3)
    return (APP.err.get_text() == "",
            "choosing a layout with nothing typed still said %r"
            % APP.err.get_text())


def no_password_tick_empties_and_dims():
    """FR-001 -- ticking this is how a machine ends up with no password, and
    it repainted the two dead fields to look exactly as live as before, still
    showing the password just typed."""
    APP.cb_show.set_active(True)
    APP.e_pw.set_text("hunter2-secret")
    APP.e_pw2.set_text("hunter2-secret")
    D.pump(0.2)
    pw_box = box_of(D, APP.e_pw)
    tick_box = box_of(D, APP.cb_none)
    live = D.shot(os.path.join(SHOTS, "pw-live.png"), "password typed")
    APP.cb_none.set_active(True)
    D.pump(0.3)
    dead = D.shot(os.path.join(SHOTS, "pw-dead.png"), "no-password ticked")
    control, _n = differing(live, dead, tick_box)
    if control == 0:
        return False, "harness: nothing repainted at all, not even the tick"
    if APP.e_pw.get_text() or APP.e_pw2.get_text():
        return False, ("the password is still on screen (%r) under a ticked "
                       "'without a password'" % APP.e_pw.get_text())
    if APP.cb_show.get_active():
        return False, "'Show password' is still on with nothing to show"
    diff, total = differing(live, dead, pw_box)
    APP.cb_none.set_active(False)
    D.pump(0.2)
    return (diff * 4 > total,
            "the PASSWORD field looks identical switched off: %d of %d pixels "
            "differ (the tick beside it moved %d)" % (diff, total, control))


def language_choice_is_answered_on_the_screen():
    """FR-002 -- the keyboard drop-down applies to the running server the
    moment it is picked; the language one changed nothing anybody could see,
    on the one screen built for somebody who may not read the language the
    machine was installed in."""
    ja = nbi18n._load_catalog("ja")
    before = D.texts()
    codes = [c for c, _ in APP._langs]
    APP.c_lang.set_active(codes.index("ja"))
    D.pump(0.4)
    after = D.texts()
    if before == after:
        return False, "nothing on the form changed: %r" % (after[:6],)
    want = [ja[k] for k in ("Setup", "NAME", "PASSWORD", "Show password",
                            "Finish", "This password cannot be recovered.")]
    missing = [w for w in want if w not in after]
    if missing:
        return False, "still not said in Japanese: %r" % (missing,)
    # A message raised AFTER the change belongs to the new language too.
    APP.e_name.set_text("bad name!")
    APP._finish()
    D.pump(0.2)
    said = APP.err.get_text()
    if said != ja["Use letters, digits and - for the name."]:
        return False, "the error line came back as %r" % said
    # ...and the placeholder, which is the only thing the NAME field says.
    got = APP.e_user.get_placeholder_text()
    if got != ja["Shown on the sign-in screen"]:
        return False, "the NAME field still offers %r" % got
    return True, ""


check("the name field says what it is for", name_field_says_what_it_is_for)
check("changing the keyboard says the password was cleared",
      keyboard_change_says_the_password_went)
check("ticking 'without a password' empties and dims the password fields",
      no_password_tick_empties_and_dims)
check("choosing a language says the whole screen again in it",
      language_choice_is_answered_on_the_screen)
D.close()


# --------------------------------------------------------------------------
# The owner's name, all the way to the disk and back out of the sign-in
# screen's own reader. A second window on a second root: Finish ends setup.
# --------------------------------------------------------------------------
LONG = "Bartholomew Alexander Fitzgerald-Montgomery the Third"      # 53 chars


def owner_name_is_kept_whole():
    """FR-003 -- the name was cut to 40 characters at write time, silently,
    while the whole of it sat on screen looking kept and the screen reported
    success. de/login.py greets what is on the disk, this screen never runs
    again, and nothing else in the OS asks."""
    d, fr, root = build("b")
    app = d.app
    app.e_user.grab_focus()
    app.e_user.set_text(LONG)
    app.e_name.set_text("note-book")
    app.cb_none.set_active(True)
    d.pump(0.2)
    on_screen = app.e_user.get_text()
    if on_screen != LONG:
        return False, ("the field itself kept only %d of %d characters: %r"
                       % (len(on_screen), len(LONG), on_screen))
    d.button("Finish").clicked()
    d.pump(0.3)
    if app.err.get_text():
        return False, "Finish did not go through: %r" % app.err.get_text()
    path = os.path.join(root, "etc/notebookos-user")
    with open(path) as fh:
        stored = fh.read().strip()
    d.close()
    if stored != LONG:
        return False, ("the disk kept %d of %d characters: %r"
                       % (len(stored), len(LONG), stored))
    # And the limit, wherever it is, is one the person can see while typing.
    import login
    login.USER_NAME_FILE = path
    greeted = login.display_name("root")
    return (greeted == LONG,
            "the sign-in screen would greet %r" % greeted)


def the_typed_limit_is_the_visible_one():
    """The same defect from the other side: a cap belongs where the person
    can see it happening, never at write time after they have been told the
    name was saved. 120 is what de/login.py reads back."""
    d, fr, root = build("c")
    cap = d.app.e_user.get_max_length()
    d.close()
    return cap == 120, "the NAME field caps typing at %r" % cap


check("the owner's name reaches the disk whole", owner_name_is_kept_whole)
check("the name field's own limit is the one the reader keeps",
      the_typed_limit_is_the_visible_one)


# --------------------------------------------------------------------------
# ...and the right-to-left check, in its own process.
# --------------------------------------------------------------------------
def rtl_in_its_own_process():
    out = real_subprocess.run([sys.executable, os.path.abspath(__file__),
                               "--rtl"], capture_output=True, text=True)
    for line in (out.stdout or "").splitlines():
        if line.startswith(("PASS ", "FAIL ")):
            print("  (yi) " + line)
    ok = out.returncode == 0 and "FAIL " not in (out.stdout or "")
    detail = (out.stdout or "")[-400:] + (out.stderr or "")[-400:]
    return ok, detail.strip().replace("\n", " | ")


check("the right-to-left drive passes in its own process",
      rtl_in_its_own_process)

print("\n%d/%d checks passed" % (sum(1 for x in R if x), len(R)))
sys.exit(0 if all(R) else 1)
