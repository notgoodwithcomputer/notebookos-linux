#!/usr/bin/env python3
"""pango_render_selftest — drive each data app's REAL drawing and printing with
non-Latin user data in it, and prove every character reached a real glyph.

    DISPLAY=:0 python3 tools/pango_render_selftest.py [app ...]

The companion to tools/toyfont_check.py, which reads the SOURCE. This one runs
it: it opens Academics on a Japanese class and renders the Schedule, exports a
Cookbook recipe / an address book / a ledger to PDF, and draws the balance
charts — each in Japanese, Chinese, Korean, Hindi and Yiddish — then asserts
that every Pango layout the app built resolved 0 unknown glyphs.

WHY BOTH. cairo's toy font API (cr.show_text) binds one FreeType face and does
no per-character fallback, and .notdef in these faces is INVISIBLE rather than a
box: the timetable's seven day headers simply were not there in five of the
seventeen shipped languages, with nothing on screen to say so. pycairo's Context
is an immutable C type and cannot be wrapped at run time, so the toy API is
caught statically (the source check) while what the code DOES take is measured
here. tofu_sweep.py answers a different question entirely — "does some shipped
face have this character" — which was true throughout and never had anything to
do with the face show_text bound. A green tofu_sweep is not evidence here.

ONE CASE PER PROCESS: each app is opened against a fresh NB_HOME, and the UI
language is pinned with $NB_LANG before nbi18n is first imported (it reads the
language once, at import).
"""
import os, sys, json, shutil

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUEST_FC = os.path.join(REPO, "tools", "guest-fonts.conf")
if os.environ.get("FONTCONFIG_FILE") != GUEST_FC:
    os.environ["FONTCONFIG_FILE"] = GUEST_FC
    os.execv(sys.executable, [sys.executable] + sys.argv)

DE = os.environ.get("NB_DE", os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"))
sys.path.insert(0, DE)

import cairo, gi
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0"); gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Pango, PangoCairo

SCRIPTS = {
    "ja": ("有機化学", "月", "醤油ラーメン", "田中 花子", "家賃の支払い"),
    "zh": ("有机化学", "周一", "红烧肉", "王小明", "房租"),
    "ko": ("유기화학", "월", "김치찌개", "김민준", "월세"),
    "hi": ("कार्बनिक रसायन", "सोम", "दाल मखनी", "अमित शर्मा", "किराया"),
    "yi": ("כעמיע", "מאָנ", "קוגל", "חנה גאָלד", "דירה־געלט"),
}

# ---- instrument BOTH text paths ------------------------------------------
# pycairo's Context is an immutable C type, so the toy API cannot be wrapped at
# runtime. It is caught STATICALLY instead (assert_no_toy_api below): a file
# with no cr.show_text / cr.text_path / cr.text_extents / cr.select_font_face in
# it cannot draw a toy-API glyph, whatever it is handed. What the render then
# proves is the other half: that the Pango path the code now takes resolves
# every character of the user's real string to a real glyph.
_unknown = [0]
_glyphs = [0]
_layouts = [0]
# Count at set_text() rather than at show time: nbprint.PdfText paints with
# show_layout_line (per wrapped line), which carries no unknown-glyph accessor.
# Both this file's helpers and PdfText set the font description BEFORE the text,
# so the count is valid the moment the text lands.
_orig_set_text = Pango.Layout.set_text


def _set_text(layout, text, length=-1):
    r = _orig_set_text(layout, text, length)
    if text:
        _layouts[0] += 1
        _glyphs[0] += len(text)
        try:
            _unknown[0] += layout.get_unknown_glyphs_count()
        except Exception:
            pass
    return r


Pango.Layout.set_text = _set_text

TOY = ("cr.show_text(", "cr.text_path(", "cr.text_extents(",
       "cr.select_font_face(", "cr.set_font_size(")


def assert_no_toy_api(*names):
    """No file under test may still reach for cairo's toy font API."""
    bad = []
    for name in names:
        src = open(os.path.join(DE, name), encoding="utf-8").read()
        # ignore the explanatory comments that name the API they replaced
        code = "\n".join(l for l in src.splitlines()
                          if not l.lstrip().startswith("#"))
        for tok in TOY:
            if tok in code:
                bad.append("%s: %s" % (name, tok))
    return bad


def fresh_home(tag):
    home = "/tmp/nbpangorender-%s" % tag
    shutil.rmtree(home, ignore_errors=True)
    cfg = os.path.join(home, ".config", "notebook")
    os.makedirs(cfg)
    os.environ["NB_HOME"] = home
    return cfg


def pump():
    n = 0
    while Gtk.events_pending() and n < 400:
        Gtk.main_iteration_do(False); n += 1


def render_widget(area, w=900, h=420):
    surf = cairo.RecordingSurface(cairo.CONTENT_COLOR_ALPHA,
                                  cairo.Rectangle(0, 0, w, h))
    cr = cairo.Context(surf)
    area.set_size_request(w, h)
    area.emit("draw", cr)


# --------------------------------------------------------------------- cases
def academics(code, cfg):
    cls, day, _d, _p, _m = SCRIPTS[code]
    json.dump({"classes": [{"label": cls, "color": "#9A7B4F", "room": "D2",
                            "meets": [{"day": 0, "start": "09:00",
                                       "end": "10:20", "room": ""}]}],
               "lectures": [{"cls": 0, "num": "01", "title": cls,
                             "date": "2026-07-27", "meta": "", "notes": "x",
                             "ranges": {}}],
               "homework": [], "active": 0},
              open(os.path.join(cfg, "academics.json"), "w"))
    import academics
    w = academics.Academics(); pump()
    w._set_view("schedule") if hasattr(w, "_set_view") else None
    pump()
    assert w.grid_area is not None, "schedule view not built"
    render_widget(w.grid_area)


def cookbook(code, cfg):
    _c, _d, dish, _p, _m = SCRIPTS[code]
    json.dump({"cats": [dish], "recipes": [
        {"title": dish, "cat": dish, "desc": dish, "time": "1h", "makes": "4",
         "effort": "Easy", "ing": dish, "steps": dish, "photo": ""}]},
        open(os.path.join(cfg, "cookbook.json"), "w"))
    import cookbook as ck
    w = ck.Cookbook(); pump()
    w._render_pdf("/tmp/nbpangorender-cookbook-%s.pdf" % code, w.recipes[0])


def contacts(code, cfg):
    _c, _d, _dish, person, note = SCRIPTS[code]
    json.dump({"people": [{"name": person, "role": note, "phone": "555",
                           "email": "", "address": note, "bday": "",
                           "notes": note}]},
              open(os.path.join(cfg, "contacts.json"), "w"))
    import contacts as ct
    w = ct.Contacts(); pump()
    w._render_pdf("/tmp/nbpangorender-contacts-%s.pdf" % code)


def accounting(code, cfg):
    _c, _d, _dish, _p, memo = SCRIPTS[code]
    json.dump({"opening": 100.0, "tx": [
        {"date": "2026-07-20", "desc": memo, "amt": -850.0},
        {"date": "2026-07-21", "desc": memo, "amt": 1200.0}]},
        open(os.path.join(cfg, "accounting.json"), "w"))
    import accounting as ac
    w = ac.Accounting(); pump()
    w._render_pdf("/tmp/nbpangorender-accounting-%s.pdf" % code)
    render_widget(w.chart, 300, 156)
    # the second, sidebar sparkline draws through _render_chart directly
    surf = cairo.RecordingSurface(cairo.CONTENT_COLOR_ALPHA,
                                  cairo.Rectangle(0, 0, 260, 80))
    w._render_chart(cairo.Context(surf), 0, 0, 260, 80,
                    w._balance_series() or [1.0, 2.0, 3.0])


CASES = {"academics": academics, "cookbook": cookbook,
         "contacts": contacts, "accounting": accounting}
SRC = {"academics": "academics.py", "cookbook": "cookbook.py",
       "contacts": "contacts.py", "accounting": "accounting.py"}

if __name__ == "__main__":
    if len(sys.argv) >= 3:
        app, code = sys.argv[1], sys.argv[2]
        # $NB_LANG must be set before nbi18n loads its catalog, which happens
        # on the first app import below. Setting it after leaves every label in
        # English and the render proves nothing.
        os.environ["NB_LANG"] = code
        cfg = fresh_home("%s-%s" % (app, code))
        CASES[app](code, cfg)
        toy = assert_no_toy_api(SRC[app])
        ok = not toy and _unknown[0] == 0 and _layouts[0] > 0
        print("%-11s %-3s pango layouts=%-4d chars=%-5d unknown=%-4d "
              "toy-api=%-4s %s"
              % (app, code, _layouts[0], _glyphs[0], _unknown[0],
                 toy or "none", "OK" if ok else "TOFU"))
        raise SystemExit(0 if ok else 1)
    import subprocess
    apps = sys.argv[1:] or list(CASES)
    bad = 0
    for app in apps:
        for code in SCRIPTS:
            r = subprocess.run([sys.executable, os.path.abspath(__file__),
                                app, code], capture_output=True, text=True,
                               timeout=300, env=dict(os.environ))
            out = (r.stdout or "").strip().splitlines()
            line = out[-1] if out else "CRASH %s %s: %s" % (
                app, code, (r.stderr or "").strip().splitlines()[-1:])
            print(line)
            if "OK" not in line:
                bad += 1
    print("\nRESULT: %s" % ("ALL CLEAN" if not bad else "%d WITH TOFU" % bad))
    raise SystemExit(0 if not bad else 1)
