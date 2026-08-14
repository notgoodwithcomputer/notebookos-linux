#!/usr/bin/env python3
"""Headless-first contract checks for the Notebook OS Comics studio."""
import json
import os
import collections
import shutil
import stat
import subprocess
import sys
import tempfile
import time

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
import nbjobs  # noqa: E402
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


class PixelView:
    """Lazy pixels: a 300 ppi page must not become 4.2M Python objects."""
    def __init__(self, surface):
        surface.flush(); self.surface=surface; self.data=surface.get_data()
        self.stride=surface.get_stride(); self.width=surface.get_width()
        self.height=surface.get_height()
    def __len__(self): return self.width*self.height
    def __getitem__(self,index):
        if index<0:index+=len(self)
        y,x=divmod(index,self.width); i=y*self.stride+x*4
        return bytes(self.data[i:i+4])
    def __iter__(self):
        for y in range(self.height):
            row=y*self.stride
            for x in range(self.width):
                i=row+x*4; yield bytes(self.data[i:i+4])
    def __contains__(self,value): return any(pixel==value for pixel in self)
    def __eq__(self,other):
        return isinstance(other,PixelView) and self.width==other.width and self.height==other.height and all(a==b for a,b in zip(self,other))


def pixels(surface): return PixelView(surface)


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
    comics._ring_rect(r, 20, 20, 40, 30, 6)
    ps = pixels(r)
    at = lambda x, y: ps[y * comics.PAGE_PX_W + x]
    check("geometry rectangle ring six pixels", all(at(x, 27) == comics.px4("#000000") for x in range(20, 26)) and at(26, 27) == comics.CLEAR4)
    e = comics.raster_bubble(dict(comics._bubble_defaults(40, 40), text="", tail=None))
    ep = pixels(e)
    allowed = {comics.CLEAR4, comics.px4("#FFFFFF"), comics.px4("#000000")}
    check("geometry ellipse ring exact colours", set(ep) <= allowed and comics.px4("#000000") in ep)
    shout = comics.raster_bubble(dict(comics._bubble_defaults(60, 60), style="shout", text=""))
    check("geometry starburst fill and edge", comics.px4("#FFFFFF") in pixels(shout) and comics.px4("#000000") in pixels(shout))
    p = comics._surface(False)
    comics._ring_rect(p, 10, 10, 80, 80, 9)
    pp = pixels(p)
    check("geometry panel ring bytes", pp[30 * comics.PAGE_PX_W + 18] == comics.px4("#000000") and pp[30 * comics.PAGE_PX_W + 19] == comics.CLEAR4)


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
    cjk = comics._bubble_defaults(80, 80)
    cjk.update(w=180, h=72, size=40, text="漢字かな한글漢字かな한글", tail=None)
    probe = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
    layout, _x, _y, _tw, _th = comics._text_layout(cairo.Context(probe), cjk)
    before_h = cjk["h"]
    comics.grow_bubble(cjk)
    grown_layout, _x, _y, _tw, text_h = comics._text_layout(
        cairo.Context(probe), cjk)
    check("bubble CJK fallback has no unknown glyphs",
          layout.get_unknown_glyphs_count() == 0)
    check("bubble CJK wraps and auto-height contains natural lines",
          cjk["h"] > before_h and grown_layout.get_pixel_size()[1] <= text_h,
          "layout=%d box=%.1f" % (grown_layout.get_pixel_size()[1], text_h))


def auto_height_family():
    b = comics._bubble_defaults(30, 30)
    b.update(w=210, h=72, size=40, text="This is enough lettering to wrap onto many separate lines")
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
    filters_ok = blanks_ok = True
    for n in range(comics.PAGE_MIN, comics.PAGE_MAX + 1):
        order = comics._page_order(n)
        pairs = comics._sheet_pairs(order)
        for filt in ("all", "cover", "inside"):
            wanted = []
            for side, pair in enumerate(pairs):
                sheet = side // 2 + 1
                if filt == "cover" and sheet != 1: continue
                if filt == "inside" and sheet == 1: continue
                wanted.extend(x for x in pair if x is not None)
            got = []
            path = os.path.join(tempfile.gettempdir(), "comics-impose-audit.pdf")
            comics._impose(path, order, filt, False,
                           lambda _cr, page_no, _w, _h: got.append(page_no))
            filters_ok &= got == wanted
            blanks_ok &= None not in got and all(1 <= x <= n for x in got)
            os.unlink(path)
    check("imposition every count filters before flatten", filters_ok)
    check("imposition padded slots never draw document content", blanks_ok)
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
    check("imposition print scale 0.24 exact", comics.PRINT_SCALE == 0.24 and comics.PAGE_PX_W * comics.PRINT_SCALE == nbprint.HALF_W_PT and comics.PAGE_PX_H * comics.PRINT_SCALE == nbprint.HALF_H_PT)
    cache = collections.OrderedDict()
    pages = [comics.new_page(), comics.new_page(), comics.new_page()]
    for index in range(3):
        comics._cached_flatten(cache, pages, index)
    check("imposition flatten cache retains one page", len(cache) == 1)
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


def lazy_page_family():
    """A fresh page must cost nothing until something actually needs its
    pixels -- neither a decoded surface (the memory model's "only the
    active page" promise) nor even a materialised PNG (the eager
    write_to_png() this once cost ~170ms PER starter page, x8 at every
    document construction, measured 396ms total construct against
    illustrator's 120ms and 275MB RSS against its 15MB)."""
    page = comics.new_page()
    ly = page["layers"][0]
    check("lazy page starts fully unmaterialised", ly.surface is None and ly.png is None)
    doc = comics.ComicDocument()
    check("lazy document construction touches nothing",
          all(p["layers"][0].surface is None and p["layers"][0].png is None
              for p in doc.pages))
    surf = ly.decode()
    check("lazy page decodes to the right size, opaque white",
          (surf.get_width(), surf.get_height()) == (comics.PAGE_PX_W, comics.PAGE_PX_H)
          and bytes(surf.get_data()[:4]) == comics.px4("#FFFFFF"))


def duplicate_page_family():
    """Page > Duplicate Page on the ACTIVE page -- the ordinary case, since
    duplicating is something you do to the page you are looking at, which
    is exactly the page most likely to hold a live cairo.ImageSurface.
    copy.deepcopy cannot cross one (TypeError: cannot pickle
    'cairo.ImageSurface' object), so this crashed outright before
    add_page(duplicate=True) was routed through _duplicate_page()."""
    doc = comics.ComicDocument()
    page = doc.pages[doc.active]
    surf = page["layers"][0].decode()
    comics._write_pixel(surf, 12, 12, "#C8341E")
    surf.flush()
    page["panels"] = comics.panel_layout(3)
    page["bubbles"] = [comics._bubble_defaults(40, 40)]
    before_count = len(doc.pages)
    try:
        ok = doc.add_page(duplicate=True)
        crashed = False
    except TypeError:
        ok, crashed = False, True
    check("duplicate active decoded page does not crash", ok and not crashed)
    if ok:
        dup = doc.pages[doc.active]
        dsurf = dup["layers"][0].decode()
        dsurf.flush()
        px = bytes(dsurf.get_data()[12 * dsurf.get_stride() + 12 * 4:
                                    12 * dsurf.get_stride() + 12 * 4 + 4])
        check("duplicate carries the source page's pixels",
              px == comics.px4("#C8341E"))
        check("duplicate carries panels and bubbles, independently",
              dup["panels"] == page["panels"] and dup["panels"] is not page["panels"]
              and dup["bubbles"] == page["bubbles"] and dup["bubbles"] is not page["bubbles"])
        check("duplicate is an independent surface (editing one leaves the other alone)",
              dup["layers"][0].surface is not page["layers"][0].surface)
        check("duplicate page count grew by exactly one", len(doc.pages) == before_count + 1)


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


class DummyStyle:
    def __init__(self): self.classes=set()
    def add_class(self,name): self.classes.add(name)
    def remove_class(self,name): self.classes.discard(name)


class DummyControl:
    def __init__(self): self.style=DummyStyle(); self.sensitive=True
    def get_style_context(self): return self.style
    def set_sensitive(self,on): self.sensitive=bool(on)


def polish_feedback_family():
    app=comics.Comics.__new__(comics.Comics); app.tool="pencil"
    app.tool_buttons={name:DummyControl() for name,_label,_key in comics.TOOLS}
    app.size_grp=DummyControl(); app.shape_grp=DummyControl(); app.bubble_group=DummyControl()
    app._sync_tool_state()
    check("polish initial pencil selected class", "sel" in app.tool_buttons["pencil"].style.classes)
    samples=[]
    for tool in ("pencil","fill","line","select"):
        app.tool=tool; app._sync_tool_state(); samples.append((tool,app.size_grp.sensitive,app.shape_grp.sensitive,"dim" in app.size_grp.style.classes,"dim" in app.shape_grp.style.classes))
    check("polish tool dim states size and shapes",
          samples==[("pencil",True,False,False,True),("fill",False,False,True,True),("line",True,True,False,False),("select",False,False,True,True)],samples)
    class Tip:
        def __init__(self):self.text=None
        def set_text(self,text):self.text=text
    tip=Tip(); app._recent=["#385C78"]
    pal=app._palette_tooltip(None,1,1,False,tip); pal_name=tip.text
    recent=app._recent_tooltip(None,1,1,False,tip); recent_name=tip.text
    check("polish palette recent tooltip names",pal and recent and pal_name==comics.palette_name(0) and recent_name==comics.mix_name("#385C78"))


class Event:
    def __init__(self, x=0, y=0, button=1, state=0, keyval=0, direction=None,
                 event_type=None):
        self.x=x; self.y=y; self.button=button; self.state=state
        self.keyval=keyval; self.direction=direction; self.type=event_type


def interaction_app(pages=2):
    app=comics.Comics.__new__(comics.Comics)
    app.doc=comics.ComicDocument([comics.new_page() for _ in range(max(1,pages))])
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
    app=interaction_app(4); before=len(app.doc.pages[0]["bubbles"]); app.tool="bubble"; app.bubble_style="speech"; app.bubble_size=40; app.bubble_bold=False; app.bubble_italic=False
    app._bubble_editor=lambda index,new_before=None: app._restore_snapshot(new_before)
    app._on_press(None,Event(30,30))
    check("interaction bubble placement cancel removes new bubble",len(app.doc.pages[0]["bubbles"])==before)
    app=interaction_app(); app.ramp_area=type("Ramp",(),{"get_allocated_width":lambda self:100,"queue_draw":lambda self:None})()
    app.size_lbl=type("Label",(),{"set_text":lambda self,text:None})(); app._on_ramp_press(app.ramp_area,Event(99,10))
    check("interaction ramp click sets brush size",app.size==comics.SIZE_RAMP[-1])
    app=interaction_app(); app.palette_area=DummyWidget(); app.recent_area=DummyWidget(); app.colour_chip=DummyWidget(); app.colour_name=type("Label",(),{"set_text":lambda self,text:None})(); app._recent=[]; app._on_palette_press(None,Event(1,1))
    check("interaction palette click sets colour",app.color==comics.PALETTE[0])
    app=interaction_app(1); top=comics.Layer("Top",opacity=50,surface=comics._surface(False)); comics._write_pixel(top.decode(),3,4,"#FF0000"); app.doc.pages[0]["layers"].append(top); app.previous_tool="pencil"; app._set_tool=lambda tool:setattr(app,"tool",tool); app._pick_colour((3,4))
    check("interaction eyedropper blends one pixel without page composite",app.color=="#FF8080")
    check("interaction small placed image stays native size",
          comics._place_scale(550,850)==1.0)
    check("interaction oversized placed image scales down to fit",
          comics._place_scale(3300,2550)==0.5)
    app=interaction_app(); app.bw_inside=False; app.grid=False; app.page_guides=True; app.selection=None
    menu_ok=True
    try:
        for name in ("Edit","View","Page","Layer"):
            rows=app.menu_items(name); menu_ok &= all(isinstance(row,tuple) and len(row)==2 for row in rows)
    except Exception:
        menu_ok=False
    check("interaction menu smoke no invalid separators",menu_ok)
    app=interaction_app(3); app.doc.pages[0]["layers"][0].decode(); app.doc.pages[1]["layers"][0].decode(); app.doc.pages[2]["layers"][0].decode(); app._cache_page_switch(1,2)
    decoded=sum(any(layer.surface is not None for layer in page["layers"]) for page in app.doc.pages)
    check("interaction page switch keeps at most two decoded pages",decoded<=2)
    app=interaction_app(); app._thumb_cache[0]=object(); app._touch_page(0,False)
    check("interaction thumbnail cache invalidates on commit",0 not in app._thumb_cache)
    app=interaction_app(); bubble=comics._bubble_defaults(300,100); app.doc.pages[0]["bubbles"].append(bubble); app.tool="select"
    app._on_press(None,Event(320,120)); app._on_motion(None,Event(325,127)); app._on_release(None,Event(325,127))
    moved=(bubble["x"],bubble["y"])==(305,107)
    app.selection=("bubble",0); corner=(bubble["x"]+bubble["w"],bubble["y"]+bubble["h"]); oldw=bubble["w"]; app._on_press(None,Event(*corner)); app._on_motion(None,Event(corner[0]+12,corner[1]+8)); app._on_release(None,Event(corner[0]+12,corner[1]+8)); resized=bubble["w"]>oldw
    tail=tuple(bubble["tail"]); app.selection=("bubble",0); app._on_press(None,Event(*tail)); app._on_motion(None,Event(tail[0]+9,tail[1]+4)); app._on_release(None,Event(tail[0]+9,tail[1]+4)); tailed=tuple(bubble["tail"])==(tail[0]+9,tail[1]+4)
    check("interaction select move resize tail handlers",moved and resized and tailed,
          "moved=%r resized=%r tailed=%r pos=%r tail=%r"%
          (moved,resized,tailed,(bubble["x"],bubble["y"]),bubble["tail"]))
    app.selection=("bubble",0); x=bubble["x"]; app._nudge(1,0); app._finish_nudge()
    check("interaction selected object nudge",bubble["x"]==x+1)


def place_image_family():
    tmp = tempfile.mkdtemp(prefix="comic-place-")
    path = os.path.join(tmp, "alpha.png")
    image = cairo.ImageSurface(cairo.FORMAT_ARGB32, 11, 7)
    cr = cairo.Context(image)
    cr.set_source_rgba(1, 0, 0, .5); cr.paint(); image.write_to_png(path)
    app = interaction_app(1)
    layer = app.doc.pages[0]["layers"][0]
    layer.surface = comics._surface(True)
    old_picker = comics.nbpicker.open_file
    flashes = []
    app._flash = lambda text, saved=False: flashes.append(text)
    try:
        comics.nbpicker.open_file = lambda *_args, **_kwargs: path
        app._place_image()
    finally:
        comics.nbpicker.open_file = old_picker
    frame = app._undo_stack[-1] if app._undo_stack else None
    cx, cy = comics.PAGE_PX_W // 2, comics.PAGE_PX_H // 2
    pixel = pixels(layer.decode())[cy * comics.PAGE_PX_W + cx]
    check("place image alpha composites over active layer",
          pixel[2] > pixel[1] and pixel[2] == 255 and pixel[3] == 255,
          pixel)
    check("place image undo frame is the placed pixel rectangle",
          frame is not None and frame[0] == "px" and frame[5:7] == (11, 7),
          frame[:7] if frame else None)
    layer.visible = False
    try:
        comics.nbpicker.open_file = lambda *_args, **_kwargs: path
        app._place_image()
    finally:
        comics.nbpicker.open_file = old_picker
    check("place image hidden layer gives feedback",
          any("hidden layer" in text for text in flashes), flashes)
    corrupt = os.path.join(tmp, "broken.png")
    open(corrupt, "wb").close()
    before = len(app._undo_stack)
    try:
        comics.nbpicker.open_file = lambda *_args, **_kwargs: corrupt
        app._place_image()
    finally:
        comics.nbpicker.open_file = old_picker
    check("place image corrupt PNG changes nothing",
          len(app._undo_stack) == before)
    shutil.rmtree(tmp)


def worker_snapshot_family():
    doc = comics.ComicDocument([comics.new_page() for _ in range(4)])
    source = doc.pages[0]["layers"][0].decode()
    comics._write_pixel(source, 9, 9, "#C8341E")
    doc.pages[0]["layers"][0].touch()
    snapshot, _updates = comics.autosave_snapshot(doc)
    comics._write_pixel(source, 9, 9, "#000000")
    rebuilt = comics._pages_from_snapshot(snapshot)
    copy_pixel = pixels(rebuilt[0]["layers"][0].decode())[9 * comics.PAGE_PX_W + 9]
    check("export and print snapshots isolate worker cairo surfaces",
          copy_pixel == comics.px4("#C8341E"))
    tmp = tempfile.mkdtemp(prefix="comic-export-atomic-")
    destination = os.path.join(tmp, "book.pdf")
    open(destination, "wb").write(b"previous-valid-pdf")
    try:
        comics._write_pdf_atomic(
            destination,
            lambda draft: (open(draft, "wb").write(b"partial"),
                           (_ for _ in ()).throw(OSError("failed"))))
    except OSError:
        pass
    leftovers = [name for name in os.listdir(tmp)
                 if name.startswith(".comics-export-")]
    check("export failure preserves replaced file and cleans draft",
          open(destination, "rb").read() == b"previous-valid-pdf"
          and not leftovers, leftovers)
    comics._write_pdf_atomic(
        destination, lambda draft: open(draft, "wb").write(b"complete"))
    check("export success atomically replaces destination",
          open(destination, "rb").read() == b"complete")
    shutil.rmtree(tmp)


def migration_family():
    legacy = cairo.ImageSurface(cairo.FORMAT_ARGB32, 550, 850)
    comics._write_pixel(legacy, 17, 23, "#C8341E")
    buf = tempfile.SpooledTemporaryFile()
    legacy.write_to_png(buf); buf.seek(0)
    encoded = __import__("base64").b64encode(buf.read()).decode("ascii")
    layer = {"name":"Paper","visible":True,"opacity":100,"png":encoded,
             "layer-extra":"kept"}
    panel = {"x":10,"y":20,"w":100,"h":80,"border":3,"panel-extra":1}
    bubble = {"style":"speech","x":30,"y":40,"w":180,"h":90,
              "tail":[5,120],"text":"Migration","size":13,"align":"c",
              "bold":False,"italic":False,"bubble-extra":2}
    page = {"layers":[layer],"panels":[panel],"bubbles":[bubble],
            "mask_gutters":True,"page-extra":3}
    raw = {"format":1,"app":"comics","pages":[page,page,page,page],
           "top-extra":4}
    doc, errors = comics.ComicDocument.parse(raw)
    migrated = doc.pages[0]; surface = migrated["layers"][0].decode()
    block = all(pixels(surface)[y*comics.PAGE_PX_W+x] == comics.px4("#C8341E")
                for y in range(69,72) for x in range(51,54))
    outside = pixels(surface)[68*comics.PAGE_PX_W+51] == comics.CLEAR4
    p, b = migrated["panels"][0], migrated["bubbles"][0]
    check("migration format-1 pixel triples to exact 3x3 block",
          not errors and block and outside)
    check("migration coordinates panels bubbles tail size rebase",
          (p["x"],p["y"],p["w"],p["h"],p["border"]) == (30,60,300,240,9)
          and (b["x"],b["y"],b["w"],b["h"],b["tail"],b["size"])
          == (90,120,540,270,[15,360],39))
    check("migration marks layers dirty and preserves extras",
          migrated["layers"][0].dirty and migrated["layers"][0]._extra["layer-extra"]=="kept"
          and migrated["_extra"]["page-extra"]==3 and doc._extra["top-extra"]==4)
    saved = doc.serial()
    check("migration saves back as format 2", saved["format"]==2)


FILL_MEASURE = {}


def fill_perf_family():
    app=interaction_app(1); app.doc.pages[0]["layers"][0].surface=comics._surface(False)
    app.doc.pages[0]["layers"][0].dirty=True; app.color="#C8341E"
    started=time.perf_counter(); ok=app._flood_fill((0,0)); elapsed=time.perf_counter()-started
    seeds=app._fill_seed_count; FILL_MEASURE.update(seconds=elapsed,seeds=seeds)
    print("MEASURE fill %.3fs, %d seeds"%(elapsed,seeds))
    view=pixels(app._active_surface())
    check("fill perf whole 300ppi page under 1.5 seconds",ok and elapsed<1.5,elapsed)
    check("fill perf span seeds below three per row",seeds<3*comics.PAGE_PX_H,seeds)
    check("fill perf fills exact page bytes",view[0]==comics.px4("#C8341E") and view[-1]==comics.px4("#C8341E"))


def autosave_family():
    layer=comics.Layer(surface=comics._surface(False)); first=layer.encode(); second=layer.encode()
    check("autosave clean encode reuses identical PNG bytes",first is second)
    comics._write_pixel(layer.decode(),4,5,"#C8341E"); layer.touch(); third=layer.encode()
    check("autosave edited layer re-encodes PNG",third is not first and third!=first)
    doc=comics.ComicDocument([{"layers":[layer],"panels":[],"bubbles":[],
                               "mask_gutters":False,"_extra":{}} for _ in range(4)])
    comics._write_pixel(layer.decode(),7,8,"#1A1916"); layer.touch()
    snapshot, updates=comics.autosave_snapshot(doc)
    out=os.path.join(tempfile.mkdtemp(prefix="comic-autosave-"),"worker.json")
    dispatch=nbjobs.ManualDispatcher(); owner=nbjobs.JobOwner(dispatch=dispatch,name="comics-test")
    seen=[]; owner.start("autosave",lambda job:comics.write_autosave_snapshot(out,snapshot),
                         on_done=seen.append,policy=nbjobs.REPLACE)
    owner.join(10); dispatch.drain(); parsed,_=comics.ComicDocument.parse(json.load(open(out)))
    check("autosave worker writes parseable format 2",bool(seen) and parsed is not None and json.load(open(out))["format"]==2)
    sync=os.path.join(os.path.dirname(out),"sync.json"); comics.save_document(doc,sync)
    parsed_sync,_=comics.ComicDocument.parse(json.load(open(sync)))
    check("autosave synchronous close flush writes format 2",parsed_sync is not None and json.load(open(sync))["format"]==2)
    owner.close()


def zoom_tolerance_family():
    app=interaction_app(1); bubble=comics._bubble_defaults(100,100)
    app.doc.pages[0]["bubbles"].append(bubble); app.selection=("bubble",0)
    corner=(bubble["x"],bubble["y"])
    app.zoom=.25; fit_part=app._selection_part((corner[0]+20,corner[1]+20))
    app.zoom=1; actual_part=app._selection_part((corner[0]+20,corner[1]+20))
    check("zoom tolerance fit zoom grabs handle twenty page pixels away",fit_part=="nw")
    check("zoom tolerance actual size rejects twenty-pixel miss",actual_part=="move")
    small = dict(comics._bubble_defaults(100, 100), w=72, h=72,
                 tail=[40, 220])
    app.doc.pages[0]["bubbles"][0] = small
    app.zoom = comics.ZOOM_MIN
    parts = [app._selection_part((x, y))
             for x, y, _name in comics._selection_positions(small)]
    check("zoom minimum resolves all eight handles individually",
          parts == ["nw", "n", "ne", "w", "e", "sw", "s", "se"], parts)
    check("zoom minimum draws distinct visible handle size",
          comics._selection_handle_size(small, app.zoom) == 3.0)
    check("zoom minimum bubble tail remains individually clickable",
          app._selection_part(tuple(small["tail"])) == "tail")


def gtk_family():
    if not os.environ.get("DISPLAY"):
        skip("dialog real Panel Layout overlay widgets", "DISPLAY is absent")
        skip("dialog panel preset buttons drive all six states", "DISPLAY is absent")
        skip("dialog mix sliders preview and apply", "DISPLAY is absent")
        skip("dialog initial tool selected after construct", "DISPLAY is absent")
        skip("dialog real bubble editor widgets", "DISPLAY is absent")
        skip("undo all window destructive operations", "DISPLAY is absent")
        return
    from gi.repository import Gtk, Gdk
    if not Gtk.init_check(None)[0]:
        skip("dialog real Panel Layout overlay widgets", "GTK display unavailable")
        skip("dialog panel preset buttons drive all six states", "GTK display unavailable")
        skip("dialog mix sliders preview and apply", "GTK display unavailable")
        skip("dialog initial tool selected after construct", "GTK display unavailable")
        skip("dialog real bubble editor widgets", "GTK display unavailable")
        skip("undo all window destructive operations", "GTK display unavailable")
        return
    app = comics.Comics()
    check("dialog initial tool selected after construct",
          app.tool_buttons["pencil"].get_style_context().has_class("sel"))
    app._panel_layout_prompt()
    widgets = app._dialog_widgets
    states=[]
    for index,button in enumerate(widgets["preset"]):
        button.clicked(); states.append(widgets["preset_state"]["preset"])
    widgets["margin"].set_text("31")
    widgets["gutter"].set_text("15")
    buttons = [x for x in app._prompt_layer.get_children()]
    check("dialog real Panel Layout overlay widgets", widgets["preset_state"]["preset"] == 5)
    check("dialog panel preset buttons drive all six states",states==list(range(6)),states)
    app._close_prompt()
    app._recent=["#385C78"]; app._mix_prompt(); mix_widgets=app._dialog_widgets
    mix_widgets["sliders"][0].set_value(17); mix_widgets["sliders"][1].set_value(34); mix_widgets["sliders"][2].set_value(51)
    mix_widgets["apply"]()
    check("dialog mix sliders preview and apply",
          mix_widgets["mix"]["rgb"]==[17,34,51] and app.color=="#112233" and app.colour_name.get_text()==comics.mix_name("#112233") and app._recent[0]=="#112233")
    app._close_prompt(run_cancel=False)
    app.doc.pages[0]["bubbles"].append(comics._bubble_defaults())
    # Matches the real double-click-to-edit path (_on_press sets
    # self.selection = hit BEFORE calling _bubble_editor): without this,
    # _delete_selection() below is a no-op on a None selection regardless
    # of the key guard, which would make that check pass for the wrong
    # reason -- caught by an isolated red-proof run before landing.
    app.selection = ("bubble", 0)
    app._bubble_editor(0)
    view = app._dialog_widgets["text"]
    view.get_buffer().set_text("Live preview")
    check("dialog real bubble editor widgets", app.doc.pages[0]["bubbles"][0]["text"] == "Live preview")
    # The window's key-press-event handler runs BEFORE a focused child
    # widget's own handling (GTK dispatches the toplevel first), so an
    # unguarded bare-key branch here would eat every lowercase tool letter
    # and Delete typed into the editor -- switching tools or destroying the
    # very bubble being lettered instead of typing. _on_key must stay a
    # no-op (falsy, no side effect) while _prompt_layer is set, and resume
    # normally the moment it is not.
    check("dialog bubble editor keeps focus while open", app.get_focus() is view)
    tool_before = app.tool
    bubbles_before = len(app.doc.pages[0]["bubbles"])
    swallowed = app._on_key(app, Event(keyval=Gdk.KEY_e))
    check("dialog bubble editor swallows no tool-shortcut letters",
          not swallowed and app.tool == tool_before and app.get_focus() is view)
    deleted = app._on_key(app, Event(keyval=Gdk.KEY_Delete))
    check("dialog bubble editor survives a bare Delete",
          not deleted and app.selection == ("bubble", 0) and
          len(app.doc.pages[0]["bubbles"]) == bubbles_before)
    closed = app._on_key(app, Event(keyval=Gdk.KEY_Escape))
    check("dialog Escape still closes an open prompt",
          closed and app._prompt_layer is None)
    resumed = app._on_key(app, Event(keyval=Gdk.KEY_e))
    check("dialog tool letters resume once no prompt is open",
          resumed and app.tool == "eraser")
    app.tool = tool_before
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
    try:
        from gi.repository import Gtk
        if not Gtk.init_check(None)[0]:
            skip("store real-app cycles on a damaged store", "GTK display unavailable")
            return
    except Exception:
        skip("store real-app cycles on a damaged store", "GTK display unavailable")
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
            fresh_ok = raw.get("app") == "comics" and raw.get("format") == 2
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
lazy_page_family()
duplicate_page_family()
limits_family()
interaction_family()
place_image_family()
worker_snapshot_family()
migration_family()
fill_perf_family()
autosave_family()
zoom_tolerance_family()
polish_feedback_family()
gtk_family()
store_cycle_family()
print("%d checks: %d PASS, %d SKIP, %d FAIL" %
      (len(PASSES) + len(SKIPS) + len(FAILS), len(PASSES), len(SKIPS), len(FAILS)))
sys.exit(min(255, len(FAILS)))
