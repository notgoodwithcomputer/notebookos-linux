#!/usr/bin/env python3
"""login_keyboard_selftest — can the password actually be TYPED?

    DISPLAY=:0 python3 tools/login_keyboard_selftest.py

WHY THIS FILE EXISTS
login_shadow_selftest.py holds the invariant "a stored hash this screen asks
about is one that can be verified". This file holds the other half of the same
promise, which nothing checked and which locked people out just as completely:

    the sign-in screen must be able to type the characters the password
    is made of.

On a Russian, Greek, Hindi or Yiddish machine the saved layout is a dual one
("ru,us"), and the half that is live when the keymap loads is the NON-LATIN
one. A password made of Latin letters — what anybody has who set the machine
up in English and changed the language afterwards, or who pressed Alt+Shift
while choosing it — could not be entered at the prompt at all. The field is
masked, so the keys produced Cyrillic invisibly and the only fact on screen
was "that password did not work", on every boot, with no way back in. The kana
layout was worse: it had no Latin half to reach even if you knew how.

So the checks here are behavioural, not cosmetic:

  * every layout the OS offers, and every reordering of it, COMPILES — run
    through the real setxkbmap with -print, which resolves the keymap without
    touching the running server;
  * every layout has a group that can type ASCII (ensure_latin), because a
    password, a file name and a search box are ASCII whatever the interface is;
  * the sign-in screen shows the live alphabet and can change it, and the
    switch reaches setxkbmap with the right argv;
  * a switch made with Alt+Shift, which this screen does not perform and
    cannot poll for, still reaches the indicator;
  * a wrong password on a non-Latin group says WHICH alphabet the keys are in;
  * the half that worked is remembered, and the machine's own layout is put
    back before the desktop starts.

NOTHING HERE LOADS A KEYMAP. nbkeyboard.apply is replaced with a recorder for
every window test: this runs on a developer's real X session, and a test that
switched the keyboard out from under them would be its own kind of lockout.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay/opt/"
                        "notebook/de")
sys.path.insert(0, DE)

TMP = tempfile.mkdtemp(prefix="nb-loginkbd-")
CHECKS = [0]
FAILURES = []


def check(cond, what):
    CHECKS[0] += 1
    if cond:
        print("  ok   %s" % what)
    else:
        FAILURES.append(what)
        print("  FAIL %s" % what)


# A real SHA-512 hash of "letmein", so the window has something to ask about.
H6 = ("$6$Xz9pQm2vKr8sT1Lc$FQ0mSyO0Yy1Yy2Zz3Aa4Bb5Cc6Dd7Ee8Ff9Gg0Hh1Ii2Jj3Kk4"
      "Ll5Mm6Nn7Oo8Pp9Qq0Rr1Ss2Tt3Uu4Vv5Ww6Xx7Yy8Zz9")


def _write(name, text):
    p = os.path.join(TMP, name)
    with open(p, "w") as fh:
        fh.write(text)
    return p


# ---------------------------------------------------------------------------
# 1. The layout codes themselves
# ---------------------------------------------------------------------------
def run_codes():
    print("\n-- what a layout code means (nbkeyboard)")
    import nbkeyboard as k

    check(k.parse("ru,us") == [("ru", ""), ("us", "")],
          "a comma separates groups")
    check(k.parse("jp(kana),us") == [("jp", "kana"), ("us", "")],
          "a variant inside a multi-group code is a VARIANT, not a layout "
          "name — the case four private copies of this parser all missed")
    check(k.parse("") == [("us", "")], "an empty code is never an empty list")
    check(all(k.parse(value) == [("us", "")]
              for value in (None, 7, ["ru", "us"], {"layout": "ru"})),
          "damaged non-text layout state falls back to a usable keyboard")
    check(k.join(k.parse("jp(kana),us")) == "jp(kana),us",
          "parse and join round-trip")
    check(k.parse("us,us,ru,us") == [("us", ""), ("ru", "")],
          "duplicate groups collapse without changing first-use order")
    check(k.xorg_parts("us,us")[2] == "",
          "duplicate groups do not enable a switch key that goes nowhere")

    check(k.is_latin("us") and k.is_latin("fr") and k.is_latin("hr"),
          "Latin layouts are Latin")
    check(k.is_latin("jp") and k.is_latin("kr"),
          "JIS and Korean type ASCII directly, which is why they ship alone")
    check(not k.is_latin("jp", "kana"),
          "the kana VARIANT does not, though its base layout does")
    check(not any(k.is_latin(l) for l in ("ru", "gr", "in", "il", "bg", "ua")),
          "Cyrillic, Greek, Devanagari and Hebrew cannot type ASCII")
    check(not k.is_latin("zz-nonesuch"),
          "an unknown layout is assumed NOT Latin: guessing Latin wrongly "
          "locks somebody out, guessing the other way costs one switcher")

    check(k.latin_index("ru,us") == 1 and k.latin_index("us") == 0,
          "latin_index finds the half that can type a password")
    check(k.latin_index("jp(kana)") == -1,
          "kana alone has no such half — the layout with no way in")
    check(k.ensure_latin("jp(kana)") == "jp(kana),us",
          "...so ensure_latin adds one, keeping kana as the live group")
    check(k.ensure_latin("ru,us") == "ru,us",
          "a code that already has one is left exactly as it is")

    check(k.reorder("ru,us", 1) == "us,ru", "reorder makes a group live")
    check(k.reorder("a,b,c", 2) == "c,a,b",
          "...and keeps the others reachable, in order")
    check(k.reorder("ru,us", 7) == "ru,us", "an impossible index changes nothing")

    check(k.xorg_parts("jp(kana),us") == ("jp,us", "kana,",
                                          "grp:alt_shift_toggle"),
          "xorg.conf gets PARALLEL layout and variant lists (the server does "
          "not parse parentheses at all)")
    check(k.xorg_parts("us") == ("us", "", ""),
          "a single layout needs no variant line and no switch key")
    args = k.xkb_args("ru,us")
    check(args[:3] == ["setxkbmap", "-layout", "ru,us"], "setxkbmap argv")
    check(args.count("-option") == 2 and "" in args,
          "the options the server already carries are CLEARED first, or a "
          "machine keeps Alt+Shift bound after moving to a single layout")
    check(args[-1] == "grp:alt_shift_toggle",
          "...and a dual layout gets its switch key")


def run_compiles():
    """Every code the OS offers, in every order, resolves to a real keymap.

    -print asks setxkbmap to resolve the keymap and write it out WITHOUT
    loading it, so this proves the argv on a developer's own session without
    changing that session's keyboard."""
    print("\n-- every shipped layout compiles, in every order")
    import nbi18n
    import nbkeyboard as k
    if not shutil.which("setxkbmap"):
        check(False, "setxkbmap is not on this host, so nothing was compiled")
        return
    bad = []
    for code, _label in nbi18n.KEYBOARDS:
        full = k.ensure_latin(code)
        orders = [full] + [k.reorder(full, i)
                           for i in range(len(k.parse(full)))]
        for c in orders:
            r = subprocess.run(k.xkb_args(c) + ["-print"],
                               capture_output=True, text=True)
            if r.returncode != 0 or "xkb_symbols" not in r.stdout:
                bad.append((c, (r.stderr or "").strip()[:80]))
    check(not bad, "all %d layouts resolve a keymap in every order%s"
          % (len(nbi18n.KEYBOARDS), "" if not bad else ": %r" % bad))

    missing = [c for c, _l in nbi18n.KEYBOARDS if k.latin_index(c) < 0]
    check(not missing,
          "no shipped layout leaves the machine unable to type ASCII%s"
          % ("" if not missing else ": %r" % missing))


def run_qwerty():
    """EVERY language reaches US QWERTY at the sign-in prompt.

    THE SECOND LOCK-OUT, reported from a real machine: Japanese. "jp" is on
    the Latin list and legitimately so — JIS types ASCII directly — so
    ensure_latin left it alone, the screen saw one group and drew no switch.
    But JIS is not where passwords are written: it moves 20 ASCII characters
    and keeps _ \\ | on <AB11>, the RO key, which a 104-key ANSI board does not
    have. An underscore in a password was not mistyped, it was UNTYPEABLE, and
    the field is masked.

    This is measured, not asserted: each layout is compiled through the real
    xkbcomp and its ASCII is compared against US, key by key.
    """
    print("\n-- every language can reach US QWERTY to type a password")
    import nbi18n
    import nbkeyboard as k

    check(k.ensure_qwerty("jp") == "jp,us",
          "Japanese gains a US half — the reported lock-out")
    check(k.ensure_latin("jp") == "jp",
          "...which ensure_latin did NOT do: this is the gap, kept as a check "
          "so the weaker guarantee cannot quietly come back")
    check(k.ensure_qwerty("us") == "us",
          "a machine already on US gains nothing and draws no switch")
    check(k.ensure_qwerty("ru,us") == "ru,us",
          "a code that already carries US is left exactly as it is")
    check(k.ensure_qwerty("jp(kana)") == "jp(kana),us",
          "kana keeps its own group first and gains the way back")
    check(k.parse(k.ensure_qwerty("ru,gr,il,jp(kana)")) ==
          [("ru", ""), ("gr", ""), ("il", ""), ("us", "")],
          "a full four-group code reserves its last valid slot for US")
    check(k.parse(k.ensure_qwerty("fr"))[0] == ("fr", ""),
          "the machine's own layout stays the LIVE one — US is appended, "
          "never substituted")

    missing = [lang for lang, code in sorted(nbi18n.DEFAULT_KB.items())
               if ("us", "") not in k.parse(k.ensure_qwerty(code))]
    check(not missing,
          "all %d interface languages reach US QWERTY at sign-in%s"
          % (len(nbi18n.DEFAULT_KB),
             "" if not missing else ": %r" % missing))

    single = [lang for lang, code in sorted(nbi18n.DEFAULT_KB.items())
              if len(k.parse(k.ensure_qwerty(code))) < 2
              and k.parse(code) != [("us", "")]]
    check(not single,
          "no language is left with one group unless it IS plain US%s"
          % ("" if not single else ": %r" % single))

    # ---- the measurement the guarantee rests on ----------------------------
    if not shutil.which("xkbcomp") or not shutil.which("setxkbmap"):
        check(False, "xkbcomp/setxkbmap missing, so no keymap was measured")
        return
    ansi = set()
    for row, n in (("AE", 12), ("AD", 12), ("AC", 11), ("AB", 10)):
        ansi |= {"%s%02d" % (row, i) for i in range(1, n + 1)}
    ansi |= {"TLDE", "BKSL", "SPCE"}
    names = {"underscore": "_", "at": "@", "asciicircum": "^", "bar": "|",
             "backslash": "\\", "grave": "`", "less": "<", "greater": ">",
             "asciitilde": "~", "colon": ":", "ampersand": "&"}
    keyline = re.compile(r"key <([A-Z0-9]+)>\s*\{([^}]*)\}", re.S)

    def ascii_keys(code):
        lays, vars_, _o = k.xorg_parts(code)
        cmd = ["setxkbmap", "-layout", lays]
        if vars_:
            cmd += ["-variant", vars_]
        p = subprocess.run(cmd + ["-print"], capture_output=True, text=True)
        c = subprocess.run(["xkbcomp", "-xkb", "-", "-"], input=p.stdout,
                           capture_output=True, text=True)
        out = {}
        for key, body in keyline.findall(c.stdout):
            for sym in re.findall(r"[A-Za-z_0-9]+", body.split("],")[0]):
                if sym in names and names[sym] not in out:
                    out[names[sym]] = key
        return out

    unreachable = {}
    for lang, code in sorted(nbi18n.DEFAULT_KB.items()):
        first = k.join([k.parse(code)[0]])
        if first == "us":
            continue
        km = ascii_keys(first)
        gone = sorted(ch for ch in names.values()
                      if ch not in km or km[ch] not in ansi)
        if gone:
            unreachable[lang] = "".join(gone)
    # This is not a failure — it is WHY the guarantee exists. It fails only if
    # a language that cannot type these characters has no US group to reach.
    stranded = [lang for lang in unreachable
                if ("us", "") not in k.parse(
                    k.ensure_qwerty(nbi18n.DEFAULT_KB[lang]))]
    check(not stranded,
          "measured on the real keymaps: %s lose characters on ANSI hardware "
          "(%s) and every one of them has a US group%s"
          % (len(unreachable),
             ", ".join("%s:%s" % (l, c) for l, c in sorted(unreachable.items()))
             or "none",
             "" if not stranded else " — STRANDED: %r" % stranded))


def run_alias():
    """A layout already written into somebody's locale.json keeps being read."""
    print("\n-- the saved layout of a machine set up before this fix")
    import json
    import importlib
    home = os.path.join(TMP, "aliashome")
    cfg = os.path.join(home, ".config", "notebook")
    os.makedirs(cfg, exist_ok=True)
    with open(os.path.join(cfg, "locale.json"), "w") as fh:
        json.dump({"lang": "ja", "keyboard": "jp(kana)"}, fh)
    old = os.environ.get("NB_HOME")
    os.environ["NB_HOME"] = home
    try:
        import nbi18n
        importlib.reload(nbi18n)
        check(nbi18n.keyboard() == "jp(kana),us",
              "a saved 'jp(kana)' now reads back with a Latin half: nothing "
              "re-asks for a layout, so the owner would never have seen it")
    finally:
        if old is None:
            os.environ.pop("NB_HOME", None)
        else:
            os.environ["NB_HOME"] = old
        import nbi18n
        importlib.reload(nbi18n)


# ---------------------------------------------------------------------------
# 2. The screen
# ---------------------------------------------------------------------------
class _Machine:
    """Stand in for nbi18n and for the X server, so a test never loads a
    keymap on the developer's own session."""

    def __init__(self, login, saved, remembered=""):
        self.login = login
        self.saved = saved
        self.remembered = remembered
        self.applied = []

    def keyboard(self):
        # The RAW saved value, deliberately: the kana case below is a machine
        # configured before nbi18n's alias existed, and the screen has to
        # survive it on its own.
        return self.saved

    def login_keyboard(self):
        return self.remembered

    def set_login_keyboard(self, code):
        self.remembered = code
        return True

    def __enter__(self):
        import nbkeyboard
        self._real_i18n = self.login.nbi18n
        self._real_apply = nbkeyboard.apply
        # the screen asks the X server what is REALLY loaded when the saved
        # layout is showing (session.sh may have fallen back to US); on the
        # developer's host that would be the developer's layouts
        self._real_live = nbkeyboard.live_code
        self.login.nbi18n = self
        nbkeyboard.apply = self._apply
        nbkeyboard.live_code = lambda *a, **k: ""
        return self

    def __exit__(self, *_e):
        import nbkeyboard
        self.login.nbi18n = self._real_i18n
        nbkeyboard.apply = self._real_apply
        nbkeyboard.live_code = self._real_live
        return False

    def _apply(self, code, timeout=10):
        self.applied.append(code)
        return True


def _key(group):
    """A key event carrying an X keyboard group, the only place a client is
    told which group is live."""
    from gi.repository import Gdk
    ev = Gdk.EventKey()
    ev.type = Gdk.EventType.KEY_PRESS
    ev.keyval = Gdk.KEY_a
    ev.group = group
    return ev


def run_window(login):
    print("\n-- the sign-in screen on a Cyrillic machine")
    import nbkeyboard as k
    from gi.repository import Gtk
    login.SHADOW = _write("shadow", "root:%s:19000::::::\n" % H6)

    with _Machine(login, "ru,us") as m:
        win = login.Login()
        check([n for n in k.group_names(win._kb_code)] ==
              ["Русский", "English (US)"],
              "both halves of the keyboard are offered, named in their own "
              "script")
        check(len(win._kb_btns) == 2, "one button per half is on screen")
        check(win._kb_btns[0].get_active() and not win._kb_btns[1].get_active(),
              "the live half is the one the machine is configured for")
        # The buttons ARE in the focus chain (a keyboard-only person may have
        # to switch alphabets before a password can be typed at all); what
        # protects a mid-password switch is the caret coming straight back
        # to the field with what was typed intact — checked below.
        check(all(b.get_can_focus() for b in win._kb_btns),
              "the buttons can be reached from the keyboard")
        check(m.applied == [],
              "nothing was loaded on the way in — session.sh already applied "
              "this layout and setxkbmap forks xkbcomp")

        warn = win._kbd_warning()
        check("Русский" in warn and "English (US)" in warn,
              "a wrong password names the alphabet the keys are in, and the "
              "one to switch to: %r" % warn)
        check("Alt+Shift" in warn, "...and the key that switches them")

        # The switch itself.
        # mid-password: four characters typed, then a switch by pressing the
        # other button (which takes focus, as a real click on it would)
        win.show_all()
        for _ in range(20):
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
        win.entry.grab_focus()
        win.entry.set_text("secr")
        win.entry.set_position(4)
        win._kb_btns[1].grab_focus()
        win._kb_btns[1].set_active(True)
        # (get_focus, not has_focus: the latter also needs the toplevel to be
        # the ACTIVE window on this display, which a test window may not be)
        check(win.get_focus() is win.entry,
              "after a switch the caret is back in the password field")
        check(win.entry.get_selection_bounds() == () and
              win.entry.get_position() == 4 and win.entry.get_text() == "secr",
              "...with what was typed intact and unselected, so the next key "
              "does not REPLACE the password so far: sel=%r pos=%r"
              % (win.entry.get_selection_bounds(), win.entry.get_position()))
        # pressing the live one again: no alphabet-less state, caret back too
        win._kb_btns[1].grab_focus()
        win._kb_btns[1].set_active(False)
        check(win._kb_btns[1].get_active() and win.get_focus() is win.entry
              and win.entry.get_selection_bounds() == (),
              "pressing the live half again keeps it lit and returns the caret")
        win.entry.set_text("")
        check(m.applied == ["us,ru"],
              "choosing English loads the layout with US first: %r"
              % (m.applied,))
        check(win._kb_active == 1, "the indicator follows")
        check(win._kbd_warning() == "",
              "and the wrong-password hint goes quiet: telling somebody "
              "typing Latin that their keyboard is Latin is noise")
        check([b.get_label() for b in win._kb_btns] ==
              ["Русский", "English (US)"],
              "the two buttons do not swap places under the cursor of "
              "somebody who is mid-password")
        check(win._kb_order == [1, 0],
              "X group 1 is now the Russian half; the screen knows it")

        # Alt+Shift, which this screen never performs and cannot poll for.
        win._on_key(win, _key(1))
        check(win._kb_active == 0,
              "a switch made with Alt+Shift reaches the indicator")
        win._on_key_release(win, _key(0))
        check(win._kb_active == 1, "...and back, on release as well as press")

        # Pressing the live one again must not leave the keyboard nowhere.
        win._kb_btns[1].set_active(False)
        check(win._kb_btns[1].get_active(),
              "the live half cannot be switched OFF into no alphabet at all")

        m.applied = []
        win._finish_keyboard(True)
        check(m.remembered == "us",
              "the half the password was typed on is remembered, so this is "
              "done once and not every morning")
        check(m.applied == ["ru,us"],
              "...and the machine's own layout is handed back for the "
              "desktop: %r" % (m.applied,))
        win.destroy()

    print("\n-- a machine that remembered")
    with _Machine(login, "ru,us", remembered="us") as m:
        win = login.Login()
        check(m.applied == ["us,ru"],
              "the remembered half is made live before anything is typed")
        check(win._kb_active == 0 and win._kb_btns[0].get_label() ==
              "English (US)", "and it is the one shown as live")
        check(win._kbd_warning() == "", "nothing to warn about")
        win.destroy()

    print("\n-- a machine with no Latin half at all (kana)")
    with _Machine(login, "jp(kana)") as m:
        win = login.Login()
        check(m.applied == ["jp(kana),us"],
              "a Latin half is added and loaded: kana maps the LETTER keys, "
              "so no password with a Latin letter or a digit in it could be "
              "typed at this prompt at all")
        check(len(win._kb_btns) == 2 and
              win._kb_btns[1].get_label() == "English (US)",
              "...and it is reachable from the screen")
        m.applied = []
        win._finish_keyboard(False)
        check(m.applied == [],
              "the Latin half is NOT taken away again on the way out: a "
              "layout that cannot type ASCII is the defect, not a setting")
        win.destroy()

    print("\n-- a single-layout machine")
    with _Machine(login, "us") as m:
        win = login.Login()
        check(win._kb_btns == [],
              "no switcher is drawn where there is nothing to choose")
        check(win._kbd_warning() == "" and m.applied == [],
              "and nothing is loaded or warned about")
        # The failure path must still be reachable with no keyboard row.
        win._try()
        check(win.error.get_text() != "", "a wrong password still reports")
        win.destroy()


def run_verbatim():
    """A layout must name ITSELF, in a language that is not English.

    Run in a subprocess because the catalog is chosen once, at nbi18n import,
    and this needs a Russian one while the rest of the file needs English.

    What it guards: nbi18n's auto-translate layer had "English (US)" in every
    catalog and rewrote the switch button to "Английский (США)" — while the
    sentence next to it, which substitutes the same name AFTER translation,
    went on saying "English (US)". The screen pointed at a button that was not
    there, in the four languages where this whole feature exists. Stamping the
    button was not enough either: a Gtk.Button is a Container, so the same
    walk translated the Label inside it a step later."""
    print("\n-- the layout names itself, in a Russian interface")
    src = r'''
import os, sys
sys.path.insert(0, %r)
os.environ["NB_LANG"] = "ru"
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
import nbi18n, nbkeyboard
b = Gtk.ToggleButton()
nbi18n.set_verbatim(b, nbkeyboard.group_name("us"))
lab = Gtk.Label(label="Sign In")
box = Gtk.Box()
box.add(b)
box.add(lab)
box.show_all()
child = b.get_child()
print(b.get_label(), "|", child.get_label() if child else "", "|",
      lab.get_label())
'''
    r = subprocess.run([sys.executable, "-c", src % DE],
                       capture_output=True, text=True)
    got = (r.stdout or "").strip()
    parts = [p.strip() for p in got.split("|")]
    check(len(parts) == 3 and parts[0] == "English (US)",
          "the button keeps the layout's own name: %r" % got)
    check(len(parts) == 3 and parts[1] == "English (US)",
          "...and so does the Label INSIDE it, which the tree walk reaches "
          "separately")
    check(len(parts) == 3 and parts[2] and parts[2] != "Sign In",
          "...while ordinary chrome beside it is still translated, so this "
          "is a stamp and not a switched-off catalog: %r"
          % (parts[2] if len(parts) == 3 else got))


def run_damaged(login):
    """The screen must appear on a machine whose de/ tree cannot answer."""
    print("\n-- a damaged de/ tree")
    real_kbd, real_i18n = login.nbkeyboard, login.nbi18n
    try:
        login.nbkeyboard = None
        login.nbi18n = None
        win = login.Login()
        check(win._kb_btns == [] and win._kbd_warning() == "",
              "no layout module: the screen still builds, with no switcher")
        win._try()
        check(win.error.get_text() != "",
              "...and still works — no prompt at all is the brick, not a "
              "missing button")
        win._finish_keyboard(True)
        check(True, "finishing up cannot raise either")
        win.destroy()
    finally:
        login.nbkeyboard, login.nbi18n = real_kbd, real_i18n


def main():
    os.environ.setdefault("NB_HOME", os.path.join(TMP, "home"))
    os.makedirs(os.environ["NB_HOME"], exist_ok=True)
    run_codes()
    run_compiles()
    run_qwerty()
    run_alias()
    run_verbatim()
    import login
    try:
        run_window(login)
    except Exception as e:                                     # noqa: BLE001
        check(False, "the sign-in screen could not be driven: %r" % e)
    try:
        run_damaged(login)
    except Exception as e:                                     # noqa: BLE001
        check(False, "the damaged-tree path could not be driven: %r" % e)

    print()
    if FAILURES:
        print("LOGIN KEYBOARD SELFTEST: %d checks, %d FAILED"
              % (CHECKS[0], len(FAILURES)))
        for f in FAILURES:
            print("   - %s" % f)
        return 1
    print("LOGIN KEYBOARD SELFTEST: %d checks, all pass" % CHECKS[0])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
