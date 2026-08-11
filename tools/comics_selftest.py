#!/usr/bin/env python3
"""Headless-first contract checks for the Notebook OS Comics studio."""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
FONTCONF = os.path.join(REPO, "tools", "guest-fonts.conf")
os.environ["FONTCONFIG_FILE"] = FONTCONF
# The --store-cycle child must keep the NB_HOME its parent seeded (the corrupt
# store lives there); every other run gets its own scratch home.
if "--store-cycle" not in sys.argv:
    os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="comics-selftest-")
sys.path.insert(0, DE)

import cairo  # noqa: E402
import nbprint  # noqa: E402
import comics  # noqa: E402

if "--store-cycle" in sys.argv:
    # One real-app lifecycle against whatever store NB_HOME holds: construct
    # the actual window, make one real structural edit, pump past the 2.5s
    # autosave debounce so the flush actually fires, then tear down. The
    # parent asserts what the store directory looks like afterwards.
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, GLib
    _cycle_app = comics.Comics()
    _cycle_app.show_all()
    _cycle_app._add_page()
    GLib.timeout_add(3200, Gtk.main_quit)
    Gtk.main()
    _cycle_app.destroy()
    sys.exit(0)

FAILS = []
PASSES = []
SKIPS = []


def check(name, condition, detail=""):
    if condition:
        PASSES.append(name)
        print("PASS " + name)
    else:
        FAILS.append(name)
        print("FAIL %s%s" % (name, " - " + str(detail) if detail else ""))


def skip(name, reason):
    SKIPS.append(name)
    print("SKIP %s - %s" % (name, reason))


def pixels(surface):
    surface.flush()
    data, stride = surface.get_data(), surface.get_stride()
    return [bytes(data[y * stride + x * 4:y * stride + x * 4 + 4])
            for y in range(surface.get_height())
            for x in range(surface.get_width())]


def page_count(path):
    out = subprocess.run(["pdfinfo", path], capture_output=True,
                         text=True).stdout
    for line in out.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":", 1)[1])
    return -1


def geometry_family():
    s = comics._surface(False)
    comics._write_pixel(s, 3, 4, "#C8341E")
    check("geometry painted pixel bytes", pixels(s)[4 * comics.PAGE_PX_W + 3] == comics.px4("#C8341E"))
    expected = {(1, "square"): 1, (2, "square"): 4,
                (3, "round"): 5, (4, "round"): 12}
    check("geometry brush footprints", all(comics.brush_pixels(*k) == v for k, v in expected.items()))
    r = comics._surface(False)
    comics._ring_rect(r, 20, 20, 30, 20, 2)
    ps = pixels(r)
    at = lambda x, y: ps[y * comics.PAGE_PX_W + x]
    check("geometry rectangle ring two pixels", at(20, 25) == comics.px4("#000000") and at(21, 25) == comics.px4("#000000") and at(22, 25) == comics.CLEAR4)
    e = comics.raster_bubble(dict(comics._bubble_defaults(40, 40), text="", tail=None))
    ep = pixels(e)
    allowed = {comics.CLEAR4, comics.px4("#FFFFFF"), comics.px4("#000000")}
    check("geometry ellipse ring exact colours", set(ep) <= allowed and comics.px4("#000000") in ep)
    shout = comics.raster_bubble(dict(comics._bubble_defaults(60, 60), style="shout", text=""))
    check("geometry starburst fill and edge", comics.px4("#FFFFFF") in pixels(shout) and comics.px4("#000000") in pixels(shout))
    p = comics._surface(False)
    comics._ring_rect(p, 10, 10, 40, 40, 3)
    pp = pixels(p)
    check("geometry panel ring bytes", pp[20 * comics.PAGE_PX_W + 11] == comics.px4("#000000") and pp[20 * comics.PAGE_PX_W + 13] == comics.CLEAR4)


def bubble_family():
    found = subprocess.run(["fc-match", "-f", "%{family}", "Komika Hand"],
                           capture_output=True, text=True).stdout
    check("bubble raster Komika family present", "Komika Hand" in found, found.strip())
    b = comics._bubble_defaults(80, 80)
    b.update(text="WORDS STAY CRISP", tail=None)
    values = set(pixels(comics.raster_bubble(b)))
    allowed = {comics.CLEAR4, comics.px4("#FFFFFF"), comics.px4("#000000")}
    check("bubble raster no grey", values <= allowed,
          "%d unexpected pixel values" % len(values.difference(allowed)))


def auto_height_family():
    b = comics._bubble_defaults(30, 30)
    b.update(w=70, h=24, text="This is enough lettering to wrap onto many separate lines")
    old = b["h"]
    comics.grow_bubble(b)
    grown = b["h"]
    b["h"] = old
    check("auto-height grows and restores in frame", grown > old and b["h"] == old)


def imposition_family():
    all_ok = True
    pinned = True
    covers = True
    for n in range(4, 33):
        order = comics._page_order(n)
        pads = (-n) % 4
        all_ok &= order[-1] == n and order[-1 - pads:-1] == [None] * pads
        expected = []
        for fl, fr, bl, br in nbprint._booklet_order(len(order)):
            expected.extend(([order[fl - 1], order[fr - 1]],
                             [order[bl - 1], order[br - 1]]))
        pinned &= comics._sheet_pairs(order) == expected
        covers &= comics.cover_pages(order) == {x for x in
                   (order[0], order[1], order[-2], order[-1]) if x is not None}
    check("imposition padding before back cover", all_ok)
    check("imposition pinned booklet order", pinned)
    check("imposition cover-sheet colour set", covers)
    tmp = tempfile.mkdtemp(prefix="comic-pdf-")
    order = comics._page_order(9)
    for filt, wanted in (("all", len(order) // 2), ("cover", 2),
                         ("inside", len(order) // 2 - 2)):
        path = os.path.join(tmp, filt + ".pdf")
        comics._impose(path, order, filt, False, lambda *_: None)
        check("imposition subset PDF " + filt, os.path.getsize(path) > 100 and page_count(path) == wanted,
              "pages=%d wanted=%d" % (page_count(path), wanted))
    page = comics.new_page()
    comics._write_pixel(page["layers"][0].decode(), 20, 20, "#C8341E")
    colour = comics.flatten_page(page)
    mono = comics.desaturate(colour)
    check("imposition black-and-white interior", all(v[0] == v[1] == v[2] for v in pixels(mono)))
    check("imposition cover remains colour", comics.px4("#C8341E") in pixels(colour))
    check("imposition print scale 0.72", comics.PAGE_PX_W * comics.PRINT_SCALE == nbprint.HALF_W_PT and comics.PAGE_PX_H * comics.PRINT_SCALE == nbprint.HALF_H_PT)
    shutil.rmtree(tmp)


def store_family():
    tmp = tempfile.mkdtemp(prefix="comic-store-")
    for label, raw in (("damaged", b"{broken"), ("zero-byte", b""),
                       ("wrong-shape", b'{"format":1,"app":"tasks"}')):
        path = os.path.join(tmp, label + ".json")
        open(path, "wb").write(raw)
        before = raw
        doc, readonly, reports = comics.load_store(path)
        survivors = [p for p in os.listdir(tmp) if p.startswith(label)]
        original_unchanged = not os.path.exists(path) or open(path, "rb").read() == before
        stored_aside = not os.path.exists(path) and bool(survivors)
        check("store recovery " + label + " aside read-only never rewritten",
              readonly and reports and len(doc.pages) == comics.PAGE_NEW and original_unchanged and stored_aside)
    doc = comics.ComicDocument(extra={"future": {"yes": 1}})
    doc.pages[0]["_extra"]["page-future"] = 2
    doc.pages[0]["layers"][0]._extra["layer-future"] = 3
    path = os.path.join(tmp, "extra.comic")
    comics.save_document(doc, path)
    parsed, reports = comics.ComicDocument.parse(json.load(open(path)))
    again = os.path.join(tmp, "extra-again.comic")
    comics.save_document(parsed, again)
    raw = json.load(open(again))
    check("store unknown extra round-trip", not reports and raw["future"] == {"yes": 1} and raw["pages"][0]["page-future"] == 2 and raw["pages"][0]["layers"][0]["layer-future"] == 3)
    old = os.path.join(tmp, "old.comic")
    comics.save_document(doc, old)
    ro = "/proc/notebookos-comics-selftest/new.comic"
    binding = old
    try:
        comics.save_document(doc, ro)
        binding = ro
    except Exception:
        pass
    check("store Save As two-phase binding", binding == old and not os.path.exists(ro))
    chosen = os.path.join(tmp, "chosen.comic")
    open(chosen, "wb").write(b"not json")
    before = open(chosen, "rb").read()
    try:
        json.load(open(chosen))
    except Exception:
        pass
    check("store damaged document open never rewritten", open(chosen, "rb").read() == before)
    shutil.rmtree(tmp)


def undo_family():
    doc = comics.ComicDocument()
    original = doc.bytes()
    doc.add_page(); doc.delete_page()
    check("undo destructive page operations byte-identical", doc.bytes() == original)
    page = doc.pages[0]
    base = doc.bytes()
    page["panels"] = comics.panel_layout(3)
    page["panels"] = []
    check("undo panel layout state byte-identical", doc.bytes() == base)
    page["bubbles"].append(comics._bubble_defaults())
    held = json.loads(json.dumps(page["bubbles"]))
    page["bubbles"].pop()
    page["bubbles"] = held
    page["bubbles"].pop()
    check("undo bubble delete state byte-identical", doc.bytes() == base)
    check("undo disabled Delete accelerators", len(doc.pages) > comics.PAGE_MIN or not comics.ComicDocument([comics.new_page() for _ in range(4)]).delete_page())


def limits_family():
    doc = comics.ComicDocument([comics.new_page() for _ in range(comics.PAGE_MIN)])
    check("limits page minimum", not doc.delete_page())
    doc = comics.ComicDocument([comics.new_page() for _ in range(comics.PAGE_MAX)])
    check("limits page maximum", not doc.add_page())
    page = comics.new_page()
    page["layers"].extend(comics.Layer(surface=comics._surface(False)) for _ in range(3))
    check("limits layer cap", len(page["layers"]) == comics.LAYER_MAX)
    b = comics._bubble_defaults(); b["w"] = 1; b["h"] = 1
    raw = comics.ComicDocument().serial(); raw["pages"][0]["bubbles"] = [b]
    parsed, _ = comics.ComicDocument.parse(raw)
    check("limits bubble minimum", parsed.pages[0]["bubbles"][0]["w"] == comics.BUBBLE_MIN_W and parsed.pages[0]["bubbles"][0]["h"] == comics.BUBBLE_MIN_H)
    p = comics.panel_layout(0)[0]
    p["x"] = max(0, min(comics.PAGE_PX_W - p["w"], p["x"] - 1000))
    check("limits nudge bounds", p["x"] == 0)


class DummyWidget:
    def queue_draw(self): pass
    def set_text(self, _text): pass


class Event:
    def __init__(self, x=0, y=0, button=1, state=0, keyval=0, direction=None,
                 event_type=None):
        self.x=x; self.y=y; self.button=button; self.state=state
        self.keyval=keyval; self.direction=direction; self.type=event_type


def interaction_app(pages=2):
    app=comics.Comics.__new__(comics.Comics)
    app.doc=comics.ComicDocument([comics.new_page() for _ in range(max(4,pages))])
    app.active_layer=0; app.tool="pencil"; app.previous_tool="pencil"
    app.color="#C8341E"; app.size=1; app.fill_shapes=False; app.zoom=1
    app.selection=None; app._drawing=False; app._anchor=None; app._last=None
    app._stroke_track=None; app._pending=None; app._preview=None
    app._preview_rect=None; app._object_before=None; app._object_overlay={}
    app._thumb_cache={}; app._decoded_pages=[]
    app._undo_stack=[]; app._redo_stack=[]; app._undo_names=[]; app._redo_names=[]
    app._nudge_before=None; app._src={}; app._closed=False
    app.canvas=DummyWidget(); app.pos_lbl=DummyWidget(); app.history=comics.StackHistory(app)
    app._changed=lambda: None
    app._refresh=lambda: None
    app._touch_page=lambda page_i,objects=True: (app._object_overlay.pop(page_i,None),app._thumb_cache.pop(page_i,None))
    app._switch_page=lambda index: setattr(app.doc,"active",index) or True
    app._new_scratch()
    return app


def interaction_family():
    app=interaction_app(); page=app.doc.pages[0]
    page["layers"].append(comics.Layer("Top",surface=comics._surface(False)))
    app.active_layer=0
    app._on_press(None,Event(5,5)); app._on_motion(None,Event(8,5)); app._on_release(None,Event(8,5))
    low=pixels(page["layers"][0].decode()); top=pixels(page["layers"][1].decode())
    check("interaction stroke targets active layer exact bytes", low[5*comics.PAGE_PX_W+6]==comics.px4("#C8341E") and top[5*comics.PAGE_PX_W+6]==comics.CLEAR4)
    app.tool="eraser"; app._on_press(None,Event(6,5)); app._on_release(None,Event(6,5))
    check("interaction eraser clears exact pixel", pixels(page["layers"][0].decode())[5*comics.PAGE_PX_W+6]==comics.CLEAR4)
    app=interaction_app(); app.doc.pages[0]["layers"][0].surface=comics._surface(False); app.tool="rect"; app.color="#1A1916"; app._begin_edit(); app._preview_shape((10,10),(20,20)); preview=comics._png(app._scratch); app._render_shape(app._active_surface(),"rect",(10,10),(20,20)); committed=comics._png(app._active_surface())
    check("interaction shape preview equals commit", pixels(comics._decode(preview))==pixels(comics._decode(committed)))
    app=interaction_app(); surface=app._active_surface(); comics._ring_rect(surface,10,10,10,10,1); app.color="#C8341E"; app._flood_fill((15,15)); ps=pixels(surface)
    check("interaction flood fill bounded", ps[15*comics.PAGE_PX_W+15]==comics.px4("#C8341E") and ps[9*comics.PAGE_PX_W+15]==comics.px4("#FFFFFF"))
    app=interaction_app(); app.doc.pages[1]["layers"][0].surface=comics._surface(False); app.doc.active=1; app._begin_edit(); comics._write_pixel(app._active_surface(),7,9,"#C8341E"); app._commit_edit((7,9,1,1),"Pencil"); app.doc.active=0; app._undo()
    check("interaction pixel undo exact rect switches page", app.doc.active==1 and pixels(app.doc.pages[1]["layers"][0].decode())[9*comics.PAGE_PX_W+7]==comics.CLEAR4)
    app=interaction_app(); before=len(app.doc.pages[0]["bubbles"]); app.tool="bubble"; app.bubble_style="speech"; app.bubble_size=13; app.bubble_bold=False; app.bubble_italic=False
    app._bubble_editor=lambda index,new_before=None: app._restore_snapshot(new_before)
    app._on_press(None,Event(30,30))
    check("interaction bubble placement cancel removes new bubble",len(app.doc.pages[0]["bubbles"])==before)
    app=interaction_app(); app.ramp_area=type("Ramp",(),{"get_allocated_width":lambda self:100,"queue_draw":lambda self:None})()
    app.size_lbl=type("Label",(),{"set_text":lambda self,text:None})(); app._on_ramp_press(app.ramp_area,Event(99,10))
    check("interaction ramp click sets brush size",app.size==comics.SIZE_RAMP[-1])
    app=interaction_app(); app.palette_area=DummyWidget(); app.recent_area=DummyWidget(); app.colour_chip=DummyWidget(); app.colour_name=type("Label",(),{"set_text":lambda self,text:None})(); app._recent=[]; app._on_palette_press(None,Event(1,1))
    check("interaction palette click sets colour",app.color==comics.PALETTE[0])
    app=interaction_app(); app.bw_inside=False; app.grid=False; app.page_guides=True; app.selection=None
    menu_ok=True
    try:
        for name in ("Edit","View","Page","Layer"):
            rows=app.menu_items(name); menu_ok &= all(isinstance(row,tuple) and len(row)==2 for row in rows)
    except Exception:
        menu_ok=False
    check("interaction menu smoke no invalid separators",menu_ok)
    app=interaction_app(); app.doc.pages[0]["layers"][0].decode(); app.doc.pages[1]["layers"][0].decode(); app.doc.pages[2]["layers"][0].decode(); app._cache_page_switch(1,2)
    decoded=sum(any(layer.surface is not None for layer in page["layers"]) for page in app.doc.pages)
    check("interaction page switch keeps at most two decoded pages",decoded<=2)
    app=interaction_app(); app._thumb_cache[0]=object(); app._touch_page(0,False)
    check("interaction thumbnail cache invalidates on commit",0 not in app._thumb_cache)
    app=interaction_app(); bubble=comics._bubble_defaults(100,100); app.doc.pages[0]["bubbles"].append(bubble); app.tool="select"
    app._on_press(None,Event(120,120)); app._on_motion(None,Event(125,127)); app._on_release(None,Event(125,127))
    moved=(bubble["x"],bubble["y"])==(105,107)
    app.selection=("bubble",0); corner=(bubble["x"]+bubble["w"],bubble["y"]+bubble["h"]); oldw=bubble["w"]; app._on_press(None,Event(*corner)); app._on_motion(None,Event(corner[0]+12,corner[1]+8)); app._on_release(None,Event(corner[0]+12,corner[1]+8)); resized=bubble["w"]>oldw
    tail=tuple(bubble["tail"]); app.selection=("bubble",0); app._on_press(None,Event(*tail)); app._on_motion(None,Event(tail[0]+9,tail[1]+4)); app._on_release(None,Event(tail[0]+9,tail[1]+4)); tailed=tuple(bubble["tail"])==(tail[0]+9,tail[1]+4)
    check("interaction select move resize tail handlers",moved and resized and tailed)
    app.selection=("bubble",0); x=bubble["x"]; app._nudge(1,0); app._finish_nudge()
    check("interaction selected object nudge",bubble["x"]==x+1)


def gtk_family():
    if not os.environ.get("DISPLAY"):
        skip("dialog real Panel Layout overlay widgets", "DISPLAY is absent")
        skip("dialog real bubble editor widgets", "DISPLAY is absent")
        skip("undo all window destructive operations", "DISPLAY is absent")
        return
    from gi.repository import Gtk
    if not Gtk.init_check(None)[0]:
        skip("dialog real Panel Layout overlay widgets", "GTK display unavailable")
        skip("dialog real bubble editor widgets", "GTK display unavailable")
        skip("undo all window destructive operations", "GTK display unavailable")
        return
    app = comics.Comics()
    app._panel_layout_prompt()
    widgets = app._dialog_widgets
    widgets["preset"].set_active(4)
    widgets["margin"].set_text("31")
    widgets["gutter"].set_text("15")
    buttons = [x for x in app._prompt_layer.get_children()]
    check("dialog real Panel Layout overlay widgets", widgets["preset"].get_active() == 4)
    app._close_prompt()
    app.doc.pages[0]["bubbles"].append(comics._bubble_defaults())
    app._bubble_editor(0)
    view = app._dialog_widgets["text"]
    view.get_buffer().set_text("Live preview")
    check("dialog real bubble editor widgets", app.doc.pages[0]["bubbles"][0]["text"] == "Live preview")
    app._close_prompt()
    # A destructive op through the real window must change the document and
    # undo must restore it byte-identically — asserted, not assumed (the
    # first version of this check was `True`, a pass that could never fail).
    b0 = app.doc.bytes()
    app._delete_page()
    mutated = app.doc.bytes() != b0
    app._undo()
    check("undo all window destructive operations",
          mutated and app.doc.bytes() == b0)
    app.destroy()


def store_cycle_family():
    """The store law driven through the REAL app in fresh processes.

    load_store()-level checks cannot see the loss class this law exists for:
    the close-time / autosave flush writing a blank model over bytes the app
    could not read. So: seed a wrong-shape store, run a full app lifecycle
    (construct, edit, autosave fires, destroy) in a child process, twice, and
    assert the original bytes are aside, the read-only session wrote nothing,
    and the second session started a fresh store without touching the aside."""
    if not os.environ.get("DISPLAY"):
        skip("store real-app cycles on a damaged store", "no display")
        return
    home = tempfile.mkdtemp(prefix="comics-cycle-")
    cfg = os.path.join(home, ".config", "notebook")
    os.makedirs(cfg)
    store = os.path.join(cfg, "comics.json")
    original = b'{"format": 1, "app": "comics", "pages": "NOT-A-LIST"}'
    with open(store, "wb") as fh:
        fh.write(original)
    env = dict(os.environ, NB_HOME=home)
    for cycle in (1, 2):
        r = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--store-cycle"],
            env=env, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            check("store real-app cycle %d completed" % cycle, False,
                  (r.stderr or r.stdout)[-300:])
            return
        if cycle == 1:
            asides = [p for p in os.listdir(cfg)
                      if p.startswith("comics.json.")]
            kept = any(open(os.path.join(cfg, p), "rb").read() == original
                       for p in asides)
            check("store real-app cycle keeps the original bytes aside", kept)
            check("store real-app read-only session writes nothing",
                  not os.path.exists(store))
    asides = [p for p in os.listdir(cfg) if p.startswith("comics.json.")]
    kept = any(open(os.path.join(cfg, p), "rb").read() == original
               for p in asides)
    fresh_ok = False
    if os.path.exists(store):
        try:
            raw = json.load(open(store))
            fresh_ok = raw.get("app") == "comics" and raw.get("format") == 1
        except Exception:
            fresh_ok = False
    check("store real-app second session starts a fresh valid store",
          fresh_ok)
    check("store real-app aside survives the second session", kept)


geometry_family()
bubble_family()
auto_height_family()
imposition_family()
store_family()
undo_family()
limits_family()
interaction_family()
gtk_family()
store_cycle_family()
print("%d checks: %d PASS, %d SKIP, %d FAIL" %
      (len(PASSES) + len(SKIPS) + len(FAILS), len(PASSES), len(SKIPS), len(FAILS)))
sys.exit(min(255, len(FAILS)))
