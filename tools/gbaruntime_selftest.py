#!/usr/bin/env python3
"""
gbaruntime_selftest — run the cartridge runtime's own arithmetic on the host.

Run as:  python3 tools/gbaruntime_selftest.py

WHY THIS EXISTS
---------------
Everything else that checks the GBA runtime checks that it COMPILES and that a
ROM comes out with a valid header. Neither says anything about whether the code
computes the right answer, and two of the runtime's hardest pieces are pure
arithmetic where being wrong looks exactly like being right:

  * the affine matrix -- a rotation that drifts across the screen instead of
    turning on the spot compiles perfectly and produces a valid ROM;
  * tile collision -- for the whole life of the SDK the generator never emitted
    `tile_solid`, so `g_has_solid` stayed 0, every tile test answered "free",
    and a tile floor stopped nothing. The ROM built. The header was valid. The
    hero fell through the world.

There is no emulator in the build loop, so these run on the host instead.

THE ONE RULE THAT MAKES IT WORTH RUNNING: the C under test is EXTRACTED FROM
runtime.c, not copied into here. A copy is a second implementation that agrees
with the first until someone edits one of them, and then the test passes while
the cartridge is broken -- which is the exact shape of the bug it exists to
catch.
"""
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RT = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay/"
                        "opt/notebook/gbaruntime")
SRC = os.path.join(RT, "runtime.c")

FAIL = []


def ok(cond, label, detail=""):
    print("  %s %s%s" % ("PASS" if cond else "FAIL", label,
                         ("  -- " + detail) if detail and not cond else ""))
    if not cond:
        FAIL.append(label)


def find_gcc():
    for base, _dirs, files in os.walk(os.path.join(ROOT, "vendor-dl")):
        if "arm-none-eabi-gcc" in files:
            return os.path.join(base, "arm-none-eabi-gcc")
    return None


def extract(name):
    """One function definition from runtime.c, by brace matching.

    Signature-agnostic on purpose: it finds `name(` at the start of a
    definition and returns from the start of that line to the closing brace. If
    the function is renamed or deleted this raises rather than silently testing
    nothing, which is the failure mode that matters -- a harness that quietly
    covers zero functions reports the same "all pass" as one that covers them
    all.
    """
    src = open(SRC, encoding="utf-8").read()
    m = re.search(r"^[A-Za-z_][^\n;=]*?\b" + re.escape(name) + r"\s*\([^;{]*\)\s*\{",
                  src, re.M)
    if not m:
        raise SystemExit("runtime.c no longer defines %s()" % name)
    i = src.index("{", m.start())
    depth = 0
    j = i
    n = len(src)
    while j < n:
        ch = src[j]
        # A brace inside a character or string literal is not a block
        # delimiter. rt_say_step tests for '{' as a character, and counting it
        # ended the extraction one brace early -- which produced a function
        # with the tail missing that still looked like C, and a compile error
        # pointing at whatever followed it.
        if ch == "'" or ch == '"':
            quote = ch
            j += 1
            while j < n and src[j] != quote:
                j += 2 if src[j] == "\\" else 1
            j += 1
            continue
        if ch == "/" and j + 1 < n and src[j + 1] == "*":
            j = src.index("*/", j) + 2
            continue
        if ch == "/" and j + 1 < n and src[j + 1] == "/":
            j = src.index("\n", j) + 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():j + 1]
        j += 1
    raise SystemExit("unbalanced braces around %s()" % name)


def run_c(body, label):
    """Compile and run a host program. Returns its stdout."""
    tmp = tempfile.mkdtemp(prefix="gbart-")
    src = os.path.join(tmp, "t.c")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write(body)
    exe = os.path.join(tmp, "t")
    r = subprocess.run(["gcc", "-O2", "-o", exe, src, "-lm"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        ok(False, "%s compiles" % label, (r.stderr or "").strip()[:200])
        return ""
    r = subprocess.run([exe], capture_output=True, text=True)
    if r.returncode != 0:
        ok(False, "%s runs" % label, (r.stderr or "").strip()[:200])
    return r.stdout


HOST_TYPES = """
/* IWRAM_CODE puts a function in the console's fast RAM. It is a placement
   decision, not behaviour, and the host has no IWRAM -- defined away so the
   extracted source compiles here unchanged. That the attribute is APPLIED is
   checked on the linked ELF instead, which is where it can actually be seen. */
#define IWRAM_CODE
#include <stdio.h>
#include <string.h>
#include <math.h>
typedef unsigned char u8; typedef unsigned short u16;
typedef signed short s16; typedef signed int s32; typedef unsigned int u32;
"""

print("\n== affine: does a rotation turn on the spot? ==")
# affine_matrix is static and takes rt_cos8/rt_sin8, which are table lookups on
# the cartridge. Supply real trigonometry so the test measures the MATRIX, not
# the table.
affine = extract("affine_matrix")
ok("inv = (256 * 256) / scale" in affine,
   "the extracted matrix still inverts the scale",
   "runtime.c changed shape; this test may be measuring something else")

prog = HOST_TYPES + """
static s32 SIN[256], COS[256];
static s32 rt_sin8(s32 d) { return SIN[d & 255]; }
static s32 rt_cos8(s32 d) { return COS[d & 255]; }
""" + affine + """
int main(void) {
    for (int i = 0; i < 256; i++) {
        double a = i * 2 * M_PI / 256.0;
        SIN[i] = (s32)(sin(a) * 256); COS[i] = (s32)(cos(a) * 256);
    }
    int drift = 0;
    for (int ang = 0; ang < 256; ang++) {
        s32 pa, pb, pc, pd;
        affine_matrix(ang, 256, &pa, &pb, &pc, &pd);
        /* rt_bg_affine anchors texture (120,80) at screen (120,80) */
        s32 X = (120 << 8) - (pa * 120 + pb * 80);
        s32 Y = (80  << 8) - (pc * 120 + pd * 80);
        /* the hardware then reads tx = X + PA*x + PB*y */
        s32 tx = (X + pa * 120 + pb * 80) >> 8;
        s32 ty = (Y + pc * 120 + pd * 80) >> 8;
        if (tx != 120 || ty != 80) drift++;
    }
    printf("drift %d\\n", drift);
    s32 pa, pb, pc, pd;
    affine_matrix(0, 512, &pa, &pb, &pc, &pd); printf("zoomin %d\\n", pa);
    affine_matrix(0, 128, &pa, &pb, &pc, &pd); printf("zoomout %d\\n", pa);
    affine_matrix(64, 256, &pa, &pb, &pc, &pd);
    printf("quarter %d %d %d %d\\n", pa, pb, pc, pd);
    affine_matrix(0, 0, &pa, &pb, &pc, &pd);   printf("zeroscale %d\\n", pa);
    return 0;
}
"""
out = dict(l.split(" ", 1) for l in run_c(prog, "affine").strip().split("\n")
           if " " in l)
ok(out.get("drift") == "0",
   "the anchor texel stays put at all 256 angles",
   "drifted at %s angles" % out.get("drift"))
ok(out.get("zoomin") == "128", "scale 512 halves the texture step (2x zoom)",
   str(out.get("zoomin")))
ok(out.get("zoomout") == "512", "scale 128 doubles it (half size)",
   str(out.get("zoomout")))
ok(out.get("quarter") == "0 -256 256 0", "a quarter turn is exact",
   str(out.get("quarter")))
# A zero scale divides by zero in the matrix. It must be clamped, not crash.
ok(out.get("zeroscale") is not None, "a zero scale does not divide by zero")

print("\n== tile collision: does a solid tile actually stop anything? ==")
cell_solid = extract("cell_solid")
box_free = extract("box_free")
prog = HOST_TYPES + """
static s32 g_room_cw, g_room_ch, g_room_w, g_room_h;
static u8  g_edge_solid = 1, g_has_solid = 0, g_solid_of[512];
static const u16* g_tiles;
""" + cell_solid + "\n" + box_free + """
static const u16 MAP[12] = { 1,1,1,1,  1,1,1,1,  2,2,2,2 };  /* floor of tile 2 */
int main(void) {
    g_room_cw = 4; g_room_ch = 3; g_room_w = 32; g_room_h = 24;
    g_tiles = MAP;
    /* the state the generator used to leave behind: no solid table at all */
    g_has_solid = 0; memset(g_solid_of, 0, sizeof g_solid_of);
    printf("bug_cell %d\\n", cell_solid(0, 2));
    printf("bug_box %d\\n", box_free(8, 8, 15, 17));
    /* the state it emits now */
    g_has_solid = 1; memset(g_solid_of, 0, sizeof g_solid_of); g_solid_of[2] = 1;
    printf("cell %d\\n", cell_solid(0, 2));
    printf("empty %d\\n", cell_solid(0, 1));
    printf("blocked %d\\n", box_free(8, 8, 15, 17));
    printf("clear %d\\n", box_free(8, 0, 15, 7));
    printf("outside_closed %d\\n", cell_solid(-1, 0));
    g_edge_solid = 0;
    printf("outside_open %d\\n", cell_solid(-1, 0));
    return 0;
}
"""
out = dict(l.split(" ", 1) for l in run_c(prog, "collision").strip().split("\n")
           if " " in l)
# The two "bug_" lines are the OLD behaviour, asserted so the fix is shown to
# change the answer. A test that only checks the new state cannot tell a fix
# from a no-op.
ok(out.get("bug_cell") == "0",
   "without a solid table a floor reads as empty (the bug)")
ok(out.get("bug_box") == "1",
   "...and a box resting on it reads as free (the bug)")
ok(out.get("cell") == "1", "with the table, a floor cell is solid")
ok(out.get("empty") == "0", "...and the cell above it is not")
ok(out.get("blocked") == "0", "a box overlapping the floor is blocked")
ok(out.get("clear") == "1", "a box clear of it is free")
ok(out.get("outside_closed") == "1", "outside a closed room is solid")
ok(out.get("outside_open") == "0", "outside an open room is not")

print("\n== warps: does a doorway fire when it should? ==")
warp = extract("rt_warp_check")
ok("l >= (s32)(w->x + w->w)" in warp,
   "the extracted check still tests overlap, not containment",
   "runtime.c changed shape")
prog = HOST_TYPES + """
typedef struct { u16 x, y, w, h; s16 room; u16 tx, ty; } nb_Warp;
typedef struct { s16 x, y; u8 active; } Instance;
typedef struct { const nb_Warp* warps; u16 nwarps; } nb_Room;
static const nb_Room* g_room;
static Instance* g_view;
static s16 g_next_room = -1;
static s32 g_arrive_x = -1, g_arrive_y = -1;
static const int nb_room_count = 4;
/* the traveller's box: a 16x16 sprite centred on its position */
static void rt_bbox_at(Instance* in, s32 x, s32 y, s32* l, s32* t,
                       s32* r, s32* b) {
    (void)in; *l = x - 8; *t = y - 8; *r = x + 7; *b = y + 7;
}
static void rt_room_goto_at(s16 room, s32 x, s32 y) {
    g_next_room = room; g_arrive_x = x; g_arrive_y = y;
}
""" + warp + """
static const nb_Warp W[] = {
    { 100, 0, 8, 8, 2, 33, 44 },      /* one tile wide */
};
static const nb_Room R = { W, 1 };
static Instance P;
static int fire(s32 x, s32 y) {
    g_next_room = -1; g_arrive_x = -1; g_arrive_y = -1;
    P.x = (s16)x; P.y = (s16)y; P.active = 1;
    g_room = &R; g_view = &P;
    rt_warp_check();
    return g_next_room;
}
int main(void) {
    printf("on %d\\n", fire(104, 4));        /* squarely on it */
    printf("arrive %d %d\\n", (int)g_arrive_x, (int)g_arrive_y);
    printf("edge %d\\n", fire(96, 4));        /* box just overlaps its left */
    printf("clear %d\\n", fire(80, 4));       /* well clear */
    printf("below %d\\n", fire(104, 40));     /* right column, wrong row */
    /* a change already pending must not be overwritten by a second warp */
    P.x = 104; P.y = 4; P.active = 1; g_room = &R; g_view = &P;
    g_next_room = 3; rt_warp_check();
    printf("pending %d\\n", g_next_room);
    /* an inactive traveller triggers nothing */
    P.active = 0; g_next_room = -1; rt_warp_check();
    printf("inactive %d\\n", g_next_room);
    return 0;
}
"""
out = dict(l.split(" ", 1) for l in run_c(prog, "warps").strip().split("\n")
           if " " in l)
ok(out.get("on") == "2", "standing on a warp changes room", str(out.get("on")))
ok(out.get("arrive") == "33 44", "...and lands where the warp says",
   str(out.get("arrive")))
# Overlap rather than containment: a door one tile wide would otherwise be
# stepped straight over by anything moving faster than its width, which reads
# as a door that works only sometimes.
ok(out.get("edge") == "2", "a box merely OVERLAPPING a one-tile door fires it",
   str(out.get("edge")))
ok(out.get("clear") == "-1", "standing clear of it does nothing")
ok(out.get("below") == "-1", "the right column but the wrong row does nothing")
ok(out.get("pending") == "3", "a room change already pending is not overwritten")
ok(out.get("inactive") == "-1", "a destroyed traveller triggers nothing")

print("\n== the profiler: does a measurement survive a wrap? ==")
_pb = extract("rt_prof_begin")
_pe = extract("rt_prof_end")
prog = HOST_TYPES + """
#define PROF_SLOTS 8
static u16 g_prof_open[PROF_SLOTS], g_prof_acc[PROF_SLOTS];
static u8 g_prof_on = 1;
static u16 TIMER;
static u16 rt_timer_read(int ch) { (void)ch; return TIMER; }
""" + _pb + "\n" + _pe + """
int main(void) {
    TIMER = 100; rt_prof_begin(0);
    TIMER = 250; rt_prof_end(0);
    printf("simple %d\\n", g_prof_acc[0]);
    /* two sections in one frame accumulate */
    TIMER = 300; rt_prof_begin(0);
    TIMER = 350; rt_prof_end(0);
    printf("accumulated %d\\n", g_prof_acc[0]);
    /* ACROSS THE WRAP. A 16-bit counter rolls over; measured with signed
       arithmetic this reports a huge negative and the overlay shows nonsense
       exactly when the frame is busiest. */
    g_prof_acc[1] = 0;
    TIMER = 65500; rt_prof_begin(1);
    TIMER = 30;    rt_prof_end(1);
    printf("wrapped %d\\n", g_prof_acc[1]);
    /* a slot out of range is ignored, not written past the array */
    rt_prof_begin(99); rt_prof_end(99);
    printf("outofrange %d\\n", 1);
    /* switched off, nothing is recorded */
    g_prof_on = 0; g_prof_acc[2] = 0;
    TIMER = 0; rt_prof_begin(2); TIMER = 500; rt_prof_end(2);
    printf("off %d\\n", g_prof_acc[2]);
    return 0;
}
"""
out = dict(l.split(" ", 1) for l in run_c(prog, "profiler").strip().split("\n")
           if " " in l)
ok(out.get("simple") == "150", "a section is measured", str(out.get("simple")))
ok(out.get("accumulated") == "200", "two sections in a frame add up",
   str(out.get("accumulated")))
# 65500 -> 30 is 66 ticks, not -65470.
ok(out.get("wrapped") == "66",
   "a section measured across the counter's wrap reports its length",
   str(out.get("wrapped")))
ok(out.get("outofrange") == "1", "a slot out of range is ignored")
ok(out.get("off") == "0", "a switched-off profiler records nothing")

print("\n== cutscenes: does a glide land exactly? ==")
_gl = extract("rt_glide")
_gs = extract("glide_step")
prog = HOST_TYPES + """
typedef signed short s16;
typedef struct { s16 x, y; s16 hspeed, vspeed, hspd8, vspd8; s16 gx, gy; u16 glide; } Instance;
""" + _gl + "\n" + _gs + """
static int run(Instance *in, int frames) {
    int i;
    for (i = 0; i < frames; i++) glide_step(in);
    return in->glide;
}
int main(void) {
    Instance a = {0};
    /* a distance that does not divide evenly by the frame count: 100 over 7
       frames is 14.28 a frame, and stepping by a precomputed 14 lands at 98 */
    a.x = 0; a.y = 0;
    rt_glide(&a, 100, 55, 7);
    run(&a, 7);
    printf("exact %d %d\\n", a.x, a.y);
    printf("finished %d\\n", a.glide);
    /* still moving part way through */
    a.x = 0; a.y = 0; rt_glide(&a, 100, 0, 10);
    run(&a, 5);
    printf("partway %d\\n", a.x > 0 && a.x < 100);
    /* backwards, and a single frame */
    a.x = 100; a.y = 100; rt_glide(&a, 0, 0, 4); run(&a, 4);
    printf("backwards %d %d\\n", a.x, a.y);
    a.x = 5; rt_glide(&a, 77, 88, 1); run(&a, 1);
    printf("one_frame %d %d\\n", a.x, a.y);
    /* zero frames is a teleport, not a division by zero */
    a.x = 1; a.y = 2; rt_glide(&a, 30, 40, 0);
    printf("instant %d %d %d\\n", a.x, a.y, a.glide);
    /* a glide overrides speed, or two things move one instance */
    a.hspeed = 9; a.vspeed = 9; rt_glide(&a, 10, 10, 5);
    printf("speed_cleared %d\\n", a.hspeed == 0 && a.vspeed == 0);
    /* stepping past the end does nothing */
    a.x = 0; rt_glide(&a, 50, 0, 3); run(&a, 20);
    printf("overrun %d %d\\n", a.x, a.glide);
    return 0;
}
"""
out = dict(l.split(" ", 1) for l in run_c(prog, "glide").strip().split("\n")
           if " " in l)
# Stepping by a precomputed amount lands 100-over-7 at 98 and the scene looks
# subtly wrong with nothing to point at. Dividing the REMAINING distance by the
# REMAINING frames cannot.
ok(out.get("exact") == "100 55",
   "a glide lands exactly on its target, not a rounded pixel short",
   str(out.get("exact")))
ok(out.get("finished") == "0", "...and stops when it arrives")
ok(out.get("partway") == "1", "it is part way there part way through")
ok(out.get("backwards") == "0 0", "it works backwards too",
   str(out.get("backwards")))
ok(out.get("one_frame") == "77 88", "one frame arrives in one frame",
   str(out.get("one_frame")))
ok(out.get("instant") == "30 40 0", "zero frames is a teleport, not a crash",
   str(out.get("instant")))
ok(out.get("speed_cleared") == "1", "a glide overrides speed")
ok(out.get("overrun") == "50 0", "stepping past the end does nothing",
   str(out.get("overrun")))

print("\n== menus: navigation, scrolling and what a redraw costs ==")
_mstep = extract("rt_menu_step")
_mopen = extract("rt_menu_open")
prog = HOST_TYPES + """
#define MENU_MAX_ROWS 8
#define KEY_UP 1
#define KEY_DOWN 2
#define KEY_A 4
#define KEY_B 8
#define NB_BLUE 1
#define NB_WHITE 0
static const char *const *g_menu_items;
static int g_menu_n, g_menu_at, g_menu_top, g_menu_rows;
static u8 g_menu_col, g_menu_row, g_menu_w, g_menu_open, g_menu_dirty;
static u8 g_menu_wrap = 1;
static int KEYS, DRAWS;
static int rt_key_pressed(int k) { return (KEYS & k) ? 1 : 0; }
static void rt_clear_box(int a,int b,int c,int d){(void)a;(void)b;(void)c;(void)d;}
static void rt_draw_panel(int a,int b,int c,int d,int e,int f)
{(void)a;(void)b;(void)c;(void)d;(void)e;(void)f;}
static void rt_draw_text_c(int a,int b,const char*c,int d)
{(void)a;(void)b;(void)c;(void)d;}
static void menu_draw(void) { DRAWS++; }
static void rt_menu_close(void) { g_menu_open = 0; }
/* Where the answer goes is covered by the SDK selftest, which checks the
   generator wires a variable to it; here the navigation is what is under test. */
static int ANSWER = -99;
static void menu_answer(int v) { ANSWER = v; }
""" + _mopen + "\n" + _mstep + """
static const char *ITEMS[12] = { "a","b","c","d","e","f","g","h","i","j","k","l" };
int main(void) {
    int r;
    /* a short list: no scrolling, and wrapping at both ends */
    rt_menu_open(ITEMS, 3, 2, 2, 10);
    KEYS = KEY_UP; r = rt_menu_step();
    printf("wrap_up %d %d\\n", g_menu_at, r);
    KEYS = KEY_DOWN; rt_menu_step();
    printf("wrap_down %d\\n", g_menu_at);
    /* wrapping off: the ends hold */
    rt_menu_open(ITEMS, 3, 2, 2, 10); g_menu_wrap = 0;
    KEYS = KEY_UP; rt_menu_step();
    printf("nowrap_up %d\\n", g_menu_at);
    g_menu_wrap = 1;
    /* a long list scrolls to keep the cursor in view, one row at a time */
    rt_menu_open(ITEMS, 12, 2, 2, 10);
    printf("rows %d\\n", g_menu_rows);
    KEYS = KEY_DOWN;
    for (r = 0; r < 8; r++) rt_menu_step();
    printf("at8 %d %d\\n", g_menu_at, g_menu_top);
    printf("in_view %d\\n",
           g_menu_at >= g_menu_top && g_menu_at < g_menu_top + g_menu_rows);
    /* a redraw only when something changed */
    rt_menu_open(ITEMS, 3, 2, 2, 10);
    KEYS = 0; DRAWS = 0;
    for (r = 0; r < 10; r++) rt_menu_step();
    printf("idle_draws %d\\n", DRAWS);
    KEYS = KEY_DOWN; rt_menu_step();
    printf("moved_draws %d\\n", DRAWS);
    /* A chooses, B cancels, and both close */
    rt_menu_open(ITEMS, 3, 2, 2, 10);
    KEYS = KEY_DOWN; rt_menu_step();
    KEYS = KEY_A; r = rt_menu_step();
    printf("chose %d %d\\n", r, g_menu_open);
    rt_menu_open(ITEMS, 3, 2, 2, 10);
    KEYS = KEY_B; r = rt_menu_step();
    printf("cancel %d %d\\n", r, g_menu_open);
    /* stepping a closed menu is not an error and not a choice */
    printf("closed %d\\n", rt_menu_step());
    /* the answer is reported once, on the frame the menu closes */
    rt_menu_open(ITEMS, 3, 2, 2, 10);
    ANSWER = -99; KEYS = KEY_A; rt_menu_step();
    printf("answered %d\\n", ANSWER);
    rt_menu_open(ITEMS, 3, 2, 2, 10);
    ANSWER = -99; KEYS = 0; rt_menu_step();
    printf("no_answer_yet %d\\n", ANSWER);
    return 0;
}
"""
out = dict(l.split(" ", 1) for l in run_c(prog, "menu").strip().split("\n")
           if " " in l)
ok(out.get("wrap_up") == "2 -1", "up from the first item wraps to the last",
   str(out.get("wrap_up")))
ok(out.get("wrap_down") == "0", "...and down from the last wraps to the first")
ok(out.get("nowrap_up") == "0", "with wrapping off, the ends hold")
ok(out.get("rows") == "8", "a long list shows eight rows", str(out.get("rows")))
# Scrolling by a whole page would lose the item the player was looking at.
ok(out.get("at8") == "8 1", "...and scrolls one row at a time",
   str(out.get("at8")))
ok(out.get("in_view") == "1", "the cursor is always on screen")
# Rewriting the panel every frame is ~200 tile writes for an identical
# picture, which on this CPU is a real fraction of the budget spent on nothing.
ok(out.get("idle_draws") == "1",
   "an idle menu draws once, not every frame", str(out.get("idle_draws")))
ok(out.get("moved_draws") == "2", "...and once more when it moves",
   str(out.get("moved_draws")))
ok(out.get("chose") == "1 0", "A returns the index and closes",
   str(out.get("chose")))
ok(out.get("cancel") == "-2 0", "B cancels and closes", str(out.get("cancel")))
ok(out.get("closed") == "-2", "stepping a closed menu is a cancel, not a choice")
ok(out.get("answered") == "0", "the answer is written when the menu closes",
   str(out.get("answered")))
# Writing it early is what makes a Step event act on a choice nobody made.
ok(out.get("no_answer_yet") == "-99",
   "...and not while it is still open", str(out.get("no_answer_yet")))

print("\n== proportional text: does a glyph land where it should? ==")
_vg = extract("vwf_glyph")
_vc = extract("vwf_clear")
_fh0 = open(os.path.join(RT, "font.h"), encoding="utf-8").read()
prog = HOST_TYPES + """
#define NB_FONT_FIRST 32
#define NB_FONT_COUNT 95
#define NB_COLOURS 8
#define VWF_COLS 26
#define VWF_ROWS 4
#define VWF_TILES (VWF_COLS * VWF_ROWS)
static const u8 nb_text_bank[NB_COLOURS] = { 15, 8, 9, 10, 11, 12, 13, 14 };
""" + re.search(r"static const u16 nb_font\[\][^;]*;", _fh0).group(0) + """
""" + re.search(r"static const u8 nb_font_w\[\][^;]*;", _fh0).group(0) + """
static u32 g_vwf[VWF_TILES * 8];
static u8  g_vwf_bank[VWF_TILES];
""" + _vc + "\n" + _vg + """
/* read pixel (X,Y) back out of the tile buffer for row 0 */
static int px(int X, int y) {
    int tile = X >> 3, tx = X & 7;
    return (g_vwf[tile * 8 + y] >> (tx * 4)) & 0xF;
}
static int ink_in_row(int y) {
    int n = 0;
    for (int X = 0; X < VWF_COLS * 8; X++) if (px(X, y)) n++;
    return n;
}
int main(void) {
    int i, adv, any;
    vwf_clear();
    /* an M advances by its own width, not by 8 */
    adv = vwf_glyph(0, 0, 'M', 0);
    printf("adv_M %d\\n", adv);
    printf("adv_i %d\\n", nb_font_w['i' - 32]);
    /* ink landed somewhere in the first tile */
    any = 0; for (i = 0; i < 8; i++) any += ink_in_row(i);
    printf("drew %d\\n", any > 0);
    /* a glyph placed ACROSS a tile boundary must appear in both tiles: this is
       the whole reason a RAM buffer exists, and getting it wrong clips every
       other letter */
    vwf_clear();
    vwf_glyph(6, 0, 'M', 0);
    { int left = 0, right = 0;
      for (i = 0; i < 8; i++) {
        for (int X = 0; X < 8; X++) if (px(X, i)) left++;
        for (int X = 8; X < 16; X++) if (px(X, i)) right++;
      }
      printf("split %d %d\\n", left > 0, right > 0); }
    /* the colour goes to the tile the pixels landed in */
    vwf_clear();
    vwf_glyph(0, 0, 'A', 2);
    printf("bank %d\\n", g_vwf_bank[0] == nb_text_bank[2]);
    /* off the right edge: clipped, not written past the buffer */
    vwf_clear();
    vwf_glyph(VWF_COLS * 8 - 2, 0, 'M', 0);
    printf("clipped %d\\n", 1);
    /* row 1 writes into row 1's tiles, not row 0's */
    vwf_clear();
    vwf_glyph(0, 1, 'A', 0);
    any = 0; for (i = 0; i < 8; i++) any += ink_in_row(i);
    printf("row0_clean %d\\n", any == 0);
    { int n = 0;
      for (i = 0; i < 8; i++)
        for (int X = 0; X < 8; X++) {
          int tile = VWF_COLS + (X >> 3);
          if ((g_vwf[tile * 8 + i] >> ((X & 7) * 4)) & 0xF) n++;
        }
      printf("row1_drawn %d\\n", n > 0); }
    return 0;
}
"""
out = dict(l.split(" ", 1) for l in run_c(prog, "vwf").strip().split("\n")
           if " " in l)
ok(out.get("adv_M") not in (None, "8"),
   "a glyph advances by its own width, not a whole cell", str(out.get("adv_M")))
ok(out.get("drew") == "1", "...and puts ink in the buffer")
# The reason the RAM buffer exists at all: a glyph lands across a tile edge,
# and VRAM only takes whole tiles. Clipping it is how every other letter loses
# its right-hand columns.
ok(out.get("split") == "1 1",
   "a glyph across a tile boundary appears in BOTH tiles",
   str(out.get("split")))
ok(out.get("bank") == "1", "the colour reaches the tile the pixels landed in")
ok(out.get("row1_drawn") == "1", "row 1 draws into row 1")
ok(out.get("row0_clean") == "1", "...and leaves row 0 alone")

print("\n== measuring text ==")
_w = extract("rt_text_width")
_cells = extract("rt_text_cells")
_fh = open(os.path.join(RT, "font.h"), encoding="utf-8").read()
ok("nb_font_w[]" in _fh, "the font carries a width for every glyph")
_wid = re.search(r"nb_font_w\[\] = \{([^}]*)\}", _fh)
_widths = [int(x) for x in _wid.group(1).split(",")] if _wid else []
ok(len(_widths) == 95, "one per glyph, ASCII 32..126", str(len(_widths)))
# A zero width would run every word together; a width over 8 would overrun the
# cell the glyph is drawn in.
ok(all(1 <= w <= 8 for w in _widths), "every width fits an 8-pixel cell",
   str(sorted(set(_widths))))
ok(_widths[0] >= 2, "the space is not zero wide", str(_widths[0]))

prog = HOST_TYPES + """
#define NB_FONT_FIRST 32
#define NB_FONT_COUNT 95
""" + re.search(r"static const u8 nb_font_w\[\][^;]*;", _fh).group(0) + """
""" + _w + "\n" + _cells + """
int main(void) {
    printf("empty %d\\n", rt_text_width(""));
    printf("null %d\\n", rt_text_width(0));
    printf("cells %d\\n", rt_text_cells("HELLO"));
    /* control codes cost no width, or a coloured line reports as too long */
    printf("coded_w %d\\n", rt_text_width("{c:3}HI") == rt_text_width("HI"));
    printf("coded_c %d\\n", rt_text_cells("{c:3}HI"));
    /* an unclosed brace is text, and must be measured as text */
    printf("unclosed %d\\n", rt_text_cells("{c:3"));
    /* a proportional measure is narrower than the fixed-cell one */
    printf("narrower %d\\n", rt_text_width("iiii") < rt_text_cells("iiii") * 8);
    return 0;
}
"""
out = dict(l.split(" ", 1) for l in run_c(prog, "measure").strip().split("\n")
           if " " in l)
ok(out.get("empty") == "0", "an empty string is zero wide")
ok(out.get("null") == "0", "...and so is nothing at all")
ok(out.get("cells") == "5", "five characters are five cells", str(out.get("cells")))
ok(out.get("coded_w") == "1", "a control code costs no pixels")
ok(out.get("coded_c") == "2", "...and no cells", str(out.get("coded_c")))
ok(out.get("unclosed") == "4", "an unclosed brace is measured as text",
   str(out.get("unclosed")))
ok(out.get("narrower") == "1",
   "a narrow glyph measures narrower than its cell")

print("\n== dialogue: control codes, and what a typo does ==")
_step = extract("rt_say_step")
_num = extract("say_num")
# The reveal loop drives real hardware, so it is exercised here against a
# recording of what it drew. What matters is the PARSING: a writer's typo must
# not erase the rest of a sentence.
prog = HOST_TYPES + """
typedef signed short s16;
#define NB_MAX_GLOBALS 32
#define NB_WHITE 0
#define KEY_A 1
#define SAY_COL 2
#define SAY_ROW 14
#define SAY_W 26
#define SAY_H 4
static s32 nb_global[NB_MAX_GLOBALS];
static const char *g_say;
static u16 g_say_at, g_say_wait;
static u8 g_say_speed = 2, g_say_hold, g_say_col, g_say_row, g_say_ink;
static s16 g_say_voice = -1;
static char OUT[512]; static int ON;
static int PRESS_A;
static int rt_key_pressed(int k) { (void)k; return PRESS_A; }
static void rt_play_sound(s16 n) { (void)n; }
static void rt_clear_box(int a,int b,int c,int d){(void)a;(void)b;(void)c;(void)d;}
static void rt_draw_panel(int a,int b,int c,int d,int e,int f)
{(void)a;(void)b;(void)c;(void)d;(void)e;(void)f;}
static int LASTROW; static char LINES[8][64]; static int LN[8];
static void rt_draw_text_c(int col,int row,const char*s,int ink)
{ (void)ink; LASTROW = row;
  if (ON < 500) OUT[ON++] = s[0];
  { int r = row - SAY_ROW; int c = col - SAY_COL;
    if (r >= 0 && r < 8 && c >= 0 && c < 63) {
        LINES[r][c] = s[0]; if (c + 1 > LN[r]) LN[r] = c + 1; } } }
static void rt_say_end(void) { g_say = 0; }
/* The proportional path is covered by its own section above; here VWF is OFF,
   so these stand in for it and the CELL path is what gets exercised. */
#define VWF_COLS 26
#define NB_FONT_FIRST 32
#define NB_FONT_COUNT 95
static const u8 nb_font_w[95] = { 3,4,4,6,6,6,6,4,5,5,6,6,4,6,4,6,
    6,6,6,6,6,6,6,6,6,6,4,4,6,6,6,6, 6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,
    6,6,6,6,6,6,6,6,6,6,6,5,6,5,6,6, 4,6,6,6,6,6,6,6,6,6,6,6,5,6,6,6,
    6,6,6,6,6,6,6,6,6,6,6,5,4,5,6 };
static u8 g_vwf_on;
static u16 g_say_px;
static void vwf_clear(void) {}
static void vwf_flush(void) {}
static int vwf_glyph(int x, int row, unsigned char c, int ink)
{ (void)x; (void)row; (void)c; (void)ink; return 8; }
""" + extract("say_word_len") + "\n" + extract("say_word_px") + "\n" + _num + """
void rt_say(const char *t) {
    g_say = t; g_say_at = 0; g_say_wait = 0; g_say_hold = 0;
    g_say_speed = 2; g_say_ink = NB_WHITE; g_say_col = 0; g_say_row = 0;
}
""" + _step + """
static const char *run(const char *text, int frames) {
    ON = 0; PRESS_A = 1;
    for (int r = 0; r < 8; r++) { LN[r] = 0; for (int c = 0; c < 64; c++) LINES[r][c] = ' '; }
    rt_say(text);
    for (int i = 0; i < frames; i++) rt_say_step();
    OUT[ON] = 0; return OUT;
}
static void dumpline(const char *tag, int r) {
    LINES[r][LN[r]] = 0;
    printf("%s %s|\\n", tag, LINES[r]);
}
int main(void) {
    nb_global[3] = 1234;
    printf("plain %s\\n", run("HI", 40));
    printf("speed0 %s\\n", run("{s:0}FASTLINE", 4));
    printf("newline %s\\n", run("{s:0}A\\nB", 4));
    printf("var %s\\n", run("{s:0}N={v:3}!", 4));
    /* a typo: an unknown code must PRINT, not swallow the rest */
    printf("typo %s\\n", run("{s:0}a{q}b", 4));
    printf("unclosed %s\\n", run("{s:0}a{s:9b", 4));
    printf("colour %s\\n", run("{s:0}a{c:3}b", 4));
    /* 26 cells wide. "Bulbasaur" must not be split across the break. */
    run("{s:0}The quick brown fox jumps over the lazy dog", 6);
    dumpline("wrap0", 0);
    dumpline("wrap1", 1);
    /* a control code inside a word costs no width */
    printf("wordlen %d\\n", say_word_len("{c:3}Bulbasaur", 0));
    /* a word longer than the box still has to go somewhere */
    run("{s:0}xx ABCDEFGHIJKLMNOPQRSTUVWXYZ0123", 6);
    dumpline("longword0", 0);
    return 0;
}
"""
out = {}
for line in run_c(prog, "dialogue").strip().split("\n"):
    if " " in line:
        k, v = line.split(" ", 1)
        out[k] = v
ok(out.get("plain") == "HI", "a plain message is revealed", repr(out.get("plain")))
ok(out.get("speed0") == "FASTLINE",
   "speed 0 puts the rest of the line up at once", repr(out.get("speed0")))
ok(out.get("newline") == "AB", "a newline moves the cursor, not the text",
   repr(out.get("newline")))
ok(out.get("var") == "N=1234!", "a global is substituted in decimal",
   repr(out.get("var")))
# THE ONE THAT MATTERS TO A WRITER.
ok(out.get("typo") == "a{q}b",
   "an unknown code prints as written rather than eating the sentence",
   repr(out.get("typo")))
ok(out.get("unclosed") == "a{s:9b",
   "...and so does an unclosed one", repr(out.get("unclosed")))
ok(out.get("colour") == "ab", "a colour code is consumed, not printed",
   repr(out.get("colour")))

# Word wrap. Breaking mid-word is the single most obvious thing a text box can
# get wrong, and it is what this engine did when it shipped an hour ago.
_w0 = (out.get("wrap0") or "").rstrip("|")
_w1 = (out.get("wrap1") or "").rstrip("|")
ok(len(_w0) <= 26, "a line does not exceed the box", "%d: %r" % (len(_w0), _w0))
ok(not _w0.endswith(("quic", "brow", "jump")),
   "a line does not end mid-word", repr(_w0))
ok(_w0.split()[-1] in ("quick", "brown", "fox", "jumps", "over", "the")
   if _w0.split() else False,
   "...it ends on a whole word", repr(_w0))
ok(bool(_w1.strip()), "the rest goes on the next line", repr(_w1))
ok(not _w1.startswith(" "), "...without a leading space", repr(_w1))
# A control code inside a word costs no width: measuring the raw characters
# would wrap a line that fits and leave a ragged edge that reads as a bug in
# the text rather than in the measurement.
ok(out.get("wordlen") == "9",
   "a word is measured without its control codes", str(out.get("wordlen")))
# A word longer than the whole box cannot be wrapped anywhere; it must still
# appear rather than vanishing into a loop.
ok(bool((out.get("longword0") or "").rstrip("|").strip()),
   "a word longer than the box is still shown",
   repr(out.get("longword0")))

print("\n== multiboot: linked where the loader puts it ==")
_GCC = find_gcc()
if not _GCC:
    print("  SKIP multiboot (no arm-none-eabi-gcc in vendor-dl)")
else:
    import shutil                                          # noqa: E402
    sys.path.insert(0, os.path.join(
        ROOT, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"))
    os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="gbamb-home-"))
    os.makedirs(os.path.join(os.environ["NB_HOME"], ".config", "notebook"),
                exist_ok=True)
    import gbabuild                                        # noqa: E402
    import gbasdk                                          # noqa: E402
    _mbdir = tempfile.mkdtemp(prefix="gbamb-")
    _ex = gbasdk.GbaSdk._example_project(None)
    os.environ["PATH"] = os.path.dirname(_GCC) + os.pathsep + os.environ["PATH"]

    def _sections(elf):
        """{name: (addr, offset)} straight out of the ELF.

        Read here rather than shelled out to objdump: which binutils happen to
        sit beside the compiler varies, and a check that silently skips because
        a tool is missing is a check that stops being run."""
        import struct as _st
        d = open(elf, "rb").read()
        shoff, = _st.unpack_from("<I", d, 0x20)
        shentsize, shnum, shstrndx = _st.unpack_from("<HHH", d, 0x2E)
        def _sh(i):
            b = shoff + i * shentsize
            name, _t, _f, addr, off, size = _st.unpack_from("<IIIIII", d, b)
            return name, addr, off, size
        _n, _a, stroff, _s = _sh(shstrndx)
        out = {}
        for i in range(shnum):
            nameoff, addr, off, size = _sh(i)
            end = d.index(b"\0", stroff + nameoff)
            nm = d[stroff + nameoff:end].decode("ascii", "replace")
            if nm.startswith("."):
                out[nm] = (addr, off)
        return out

    _ok, _cart, _log = gbabuild.build_rom(_ex, _mbdir, runtime_dir=RT,
                                          toolchain_dir="/nonexistent")
    ok(_ok, "a cartridge image still builds", (_log or "")[-200:])
    _ok2, _mb, _log2 = gbabuild.build_rom(_ex, _mbdir, runtime_dir=RT,
                                          toolchain_dir="/nonexistent",
                                          multiboot=True)
    ok(_ok2, "a multiboot image builds", (_log2 or "")[-200:])
    if _ok and _ok2:
        ok(os.path.basename(_mb) == "game.mb",
           "...to its own file, not over the cartridge one",
           os.path.basename(_mb))
        _cs = _sections(os.path.join(_mbdir, "game.elf"))
        _ms = _sections(os.path.join(_mbdir, "game_mb.elf"))
        # The whole point: a multiboot image runs from EWRAM because that is
        # where the loader puts it. Linked at 0x08000000 it would build, send,
        # and jump into nothing.
        ok(_cs[".text"][0] >> 24 == 0x08,
           "the cartridge image runs from ROM", hex(_cs[".text"][0]))
        ok(_ms[".text"][0] >> 24 == 0x02,
           "the multiboot image runs from EWRAM", hex(_ms[".text"][0]))
        ok(_ms[".gbaheader"][0] == 0x02000000,
           "...header included", hex(_ms[".gbaheader"][0]))
        # .data still lives in fast IWRAM at run time; only where it is LOADED
        # FROM changes, and crt0 copies it either way.
        ok(_ms[".data"][0] >> 24 == 0x03,
           "...with data still in IWRAM at run time", hex(_ms[".data"][0]))
        # Where .data is loaded FROM differs between the two builds; the ELF
        # section header carries only the run address, so the distinction that
        # matters here is that .text moved and .data did not.
        ok(_cs[".data"][0] == _ms[".data"][0],
           "...and data runs at the same address in both builds",
           "%s vs %s" % (hex(_cs[".data"][0]), hex(_ms[".data"][0])))

    # Hot code in IWRAM. A section that is PLACED but never COPIED is a jump
    # into whatever IWRAM happened to hold, so the addresses and the copy
    # range are both checked.
    if _ok:
        _cs2 = _sections(os.path.join(_mbdir, "game.elf"))
        ok(".iwram" in _cs2, "the runtime has an IWRAM code section",
           str(sorted(_cs2)))
        if ".iwram" in _cs2:
            ok(_cs2[".iwram"][0] >> 24 == 0x03,
               "...placed in IWRAM", hex(_cs2[".iwram"][0]))
            ok(_cs2[".text"][0] >> 24 == 0x08,
               "...while the rest stays in ROM", hex(_cs2[".text"][0]))
            # .iwram and .data must be adjacent, because crt0 copies ONE range
            # and a gap between them would leave the code uncopied.
            ok(_cs2[".data"][0] > _cs2[".iwram"][0],
               "...directly before the data it shares a copy with",
               "%s vs %s" % (hex(_cs2[".iwram"][0]), hex(_cs2[".data"][0])))

    # The size limit must REFUSE. An oversized image links, writes a file and
    # is then silently not sent, which looks like a cable fault.
    _real = gbabuild.MULTIBOOT_MAX
    gbabuild.MULTIBOOT_MAX = 1024
    _ok3, _mb3, _log3 = gbabuild.build_rom(_ex, _mbdir, runtime_dir=RT,
                                           toolchain_dir="/nonexistent",
                                           multiboot=True)
    gbabuild.MULTIBOOT_MAX = _real
    ok(not _ok3, "an image too big to send is refused, not written")
    ok("cable" in (_log3 or ""), "...saying why", (_log3 or "")[-120:])
    shutil.rmtree(_mbdir, ignore_errors=True)

print("\n== cartridge clock: encoding, BCD, and an absent chip ==")
# The bit-banged transfer needs the chip and is NOT tested here. What IS
# testable is everything around it, and that is where an absent clock turns
# into a game that confidently believes it is midnight on the 1st of January.
_cmd = extract("rtc_cmd")
_bcd = extract("bcd_to_bin")
_sane = extract("rtc_sane")
prog = HOST_TYPES + _cmd + "\n" + _bcd + "\n" + _sane + """
int main(void) {
    /* fixed 0110 prefix, command in bits 3-1, read in bit 0 */
    printf("date_read %d\\n", rtc_cmd(2, 1));
    printf("date_write %d\\n", rtc_cmd(2, 0));
    printf("reset %d\\n", rtc_cmd(0, 0));
    printf("prefix_kept %d\\n", (rtc_cmd(7, 1) & 0xF0) == 0x60);
    printf("bcd59 %d\\n", bcd_to_bin(0x59));
    printf("bcd00 %d\\n", bcd_to_bin(0x00));
    printf("bcd23 %d\\n", bcd_to_bin(0x23));
    { /* a real date */
      unsigned char ok[7] = { 25, 8, 4, 1, 7, 13, 0 };
      printf("real %d\\n", rtc_sane(ok));
    }
    { /* a floating bus: 0xFF everywhere, converted from BCD */
      unsigned char no[7]; int i;
      for (i = 0; i < 7; i++) no[i] = bcd_to_bin(0x7F);
      printf("absent %d\\n", rtc_sane(no));
    }
    { unsigned char m[7] = { 25, 13, 4, 1, 7, 13, 0 }; printf("month13 %d\\n", rtc_sane(m)); }
    { unsigned char d[7] = { 25, 8, 0, 1, 7, 13, 0 };  printf("day0 %d\\n", rtc_sane(d)); }
    { unsigned char h[7] = { 25, 8, 4, 1, 24, 13, 0 }; printf("hour24 %d\\n", rtc_sane(h)); }
    { unsigned char s[7] = { 25, 8, 4, 1, 7, 13, 60 }; printf("sec60 %d\\n", rtc_sane(s)); }
    return 0;
}
"""
out = dict(l.split(" ", 1) for l in run_c(prog, "rtc").strip().split("\n")
           if " " in l)
# 0x60 | (2 << 1) | 1 = 0x65 = 101
ok(out.get("date_read") == "101", "the date-read command encodes correctly",
   str(out.get("date_read")))
ok(out.get("date_write") == "100", "...and the write form differs only in bit 0")
ok(out.get("reset") == "96", "...and reset is the bare prefix")
# A wrong prefix makes the chip ignore everything, and the clock simply never
# advances -- no error, no clue.
ok(out.get("prefix_kept") == "1", "the fixed 0110 prefix survives any command")
ok(out.get("bcd59") == "59", "BCD 0x59 is 59, not 89", str(out.get("bcd59")))
ok(out.get("bcd00") == "0", "BCD 0x00 is 0")
ok(out.get("bcd23") == "23", "BCD 0x23 is 23")
ok(out.get("real") == "1", "a real date is accepted")
# THE ONE THAT MATTERS: a cartridge with no clock does not answer with an
# error, it answers with whatever the bus floats to.
ok(out.get("absent") == "0",
   "a cartridge with no clock is REJECTED, not believed",
   str(out.get("absent")))
for _k, _lbl in (("month13", "a 13th month"), ("day0", "a zeroth day"),
                 ("hour24", "hour 24"), ("sec60", "second 60")):
    ok(out.get(_k) == "0", "%s is rejected" % _lbl, str(out.get(_k)))

print("\n== link cable: mode bits and the error path ==")
_open = extract("rt_link_open")
_poll = extract("rt_link_poll")
# Both registers select the mode and both matter: RCNT bits 15-14 choose SIO at
# all, and only then do SIOCNT bits 13-12 choose multiplayer. Setting SIOCNT
# alone leaves an RTC cartridge's port in GPIO, where the link does nothing and
# says nothing.
ok("REG_RCNT = RCNT_SIO;" in _open, "rt_link_open sets RCNT before SIOCNT")
ok(_open.index("REG_RCNT") < _open.index("REG_SIOCNT"),
   "...in that order", "SIOCNT is written first, so the mode may not take")
# A failed transfer leaves the PREVIOUS words in the registers. Reading without
# checking hands the game last frame's input as this frame's, and two units
# drift apart with nothing to show for it.
ok(_poll.index("SIO_ERROR") < _poll.index("REG_SIOMULTI0"),
   "rt_link_poll checks the error flag before reading data")
# What the code can guarantee is that it does not READ on error. Whether the
# flag is write-1-to-clear is not something this codebase has verified on
# hardware, so it does not act as though it knows.
ok("g_link_in[0] = REG_SIOMULTI0;" in _poll,
   "...and only reads the data once past that check")

prog = HOST_TYPES + """
static u16 REG_RCNT, REG_SIOCNT, REG_SIOMLT_SEND;
static u16 REG_SIOMULTI0, REG_SIOMULTI1, REG_SIOMULTI2, REG_SIOMULTI3;
#define RCNT_SIO 0x0000
#define SIO_MULTI 0x2000
#define SIO_9600 0x0000
#define SIO_115200 0x0003
#define SIO_SI_CHILD 0x0004
#define SIO_SD_READY 0x0008
#define SIO_ID_MASK 0x0030
#define SIO_ID_SHIFT 4
#define SIO_ERROR 0x0040
#define SIO_START 0x0080
static u16 g_link_in[4];
static u8  g_link_ok;
""" + _open + """
int rt_link_parent(void) { return (REG_SIOCNT & SIO_SI_CHILD) ? 0 : 1; }
int rt_link_busy(void) { return (REG_SIOCNT & SIO_START) ? 1 : 0; }
int rt_link_id(void) { return (REG_SIOCNT & SIO_ID_MASK) >> SIO_ID_SHIFT; }
""" + _poll + """
int main(void) {
    REG_SIOCNT = 0; REG_RCNT = 0x8000;      /* start in GPIO, as an RTC cart is */
    rt_link_open(SIO_115200);
    printf("rcnt %d\\n", REG_RCNT);
    printf("mode %d\\n", (REG_SIOCNT & 0x3000) == SIO_MULTI);
    printf("baud %d\\n", REG_SIOCNT & 3);
    /* the ID and parent bits are READ-ONLY: open must not have set them */
    printf("id %d\\n", rt_link_id());
    /* a transfer still running yields nothing */
    REG_SIOCNT |= SIO_START;
    printf("busy_poll %d\\n", rt_link_poll());
    /* a finished transfer with the error flag set must NOT be read */
    REG_SIOCNT &= (u16)~SIO_START; REG_SIOCNT |= SIO_ERROR;
    REG_SIOMULTI0 = 0x1234;
    printf("error_poll %d\\n", rt_link_poll());
    printf("error_cleared %d\\n", (REG_SIOCNT & SIO_ERROR) ? 1 : 0);
    printf("stale %d\\n", g_link_in[0]);
    /* and a clean one is */
    REG_SIOCNT &= (u16)~SIO_ERROR;
    REG_SIOMULTI0 = 0x1234; REG_SIOMULTI1 = 0x5678;
    REG_SIOMULTI2 = 0xFFFF; REG_SIOMULTI3 = 0xFFFF;
    printf("good_poll %d\\n", rt_link_poll());
    printf("word0 %d\\n", g_link_in[0]);
    return 0;
}
"""
out = dict(l.split(" ", 1) for l in run_c(prog, "link").strip().split("\n")
           if " " in l)
ok(out.get("rcnt") == "0", "opening the link takes the port out of GPIO",
   str(out.get("rcnt")))
ok(out.get("mode") == "1", "...into multiplayer mode")
ok(out.get("baud") == "3", "...at the baud asked for", str(out.get("baud")))
ok(out.get("id") == "0", "...without writing the read-only ID field")
ok(out.get("busy_poll") == "0", "a transfer still running yields no data")
ok(out.get("error_poll") == "0", "a failed transfer yields no data")
ok(out.get("error_cleared") == "1",
   "...and the flag is left for the hardware, not guessed at",
   str(out.get("error_cleared")))
ok(out.get("stale") == "0",
   "...leaving no stale word to be mistaken for this frame's input",
   str(out.get("stale")))
ok(out.get("good_poll") == "1", "a clean transfer yields data")
ok(out.get("word0") == "4660", "...the word each unit sent", str(out.get("word0")))

print("\n== sfx priority: does a footstep cut off a death? ==")
may = extract("sfx_may_start")
prog = HOST_TYPES + """
typedef signed short s16;
static s16 g_sfxt = -1, g_fx = -1;
static u8 g_sfx_prio;
""" + may + """
int main(void) {
    /* nothing playing: anything starts */
    g_sfxt = -1; g_fx = -1; g_sfx_prio = 0;
    printf("idle %d\\n", sfx_may_start(0));
    /* a death (5) is sounding */
    g_sfxt = 3; g_sfx_prio = 5;
    printf("footstep_over_death %d\\n", sfx_may_start(0));
    printf("explosion_over_death %d\\n", sfx_may_start(6));
    /* equal priority still wins, or a gun firing twice is heard once */
    printf("same_again %d\\n", sfx_may_start(5));
    return 0;
}
"""
out = dict(l.split(" ", 1) for l in run_c(prog, "sfx priority").strip().split("\n")
           if " " in l)
ok(out.get("idle") == "1", "an idle channel accepts anything")
ok(out.get("footstep_over_death") == "0",
   "a low-priority effect does NOT cut off a high-priority one",
   str(out.get("footstep_over_death")))
ok(out.get("explosion_over_death") == "1", "a higher one does")
# Equal wins on purpose: a gun that fires twice must be heard twice.
ok(out.get("same_again") == "1", "an equal one restarts rather than being lost")

print("\n== every struct field is actually emitted ==")
# THE CHECK THAT WOULD HAVE CAUGHT ALL OF TONIGHT'S BUGS.
#
# runtime.h grows by APPENDING fields, with "0 means the old behaviour" as the
# rule that keeps old generated code compiling. That rule is also what makes
# forgetting to emit a field silent: the C compiles, the ROM builds, the header
# is valid, and the feature simply is not there. It has happened four times --
# tile collision, the parallax layer, the open-edge room, and every one of the
# five audio fields.
#
# So: count the fields each struct declares, count what the generator writes
# into each initialiser, and require them to match.
HDR = open(os.path.join(RT, "runtime.h"), encoding="utf-8").read()


def struct_fields(name):
    """How many values one initialiser of `name` must carry."""
    # Anchor on the CLOSING line and walk back to the nearest opening brace.
    # A forward non-greedy match starts at the first `typedef struct {` in the
    # file and runs to this struct's closing line, swallowing every struct in
    # between -- which counts 51 fields for a struct that has 13 and makes the
    # check useless in the direction that matters.
    end = re.search(r"\}\s*" + re.escape(name) + r"\s*;", HDR)
    if not end:
        raise SystemExit("runtime.h no longer declares %s" % name)
    start = HDR.rfind("typedef struct", 0, end.start())
    if start < 0:
        raise SystemExit("%s is not a typedef struct" % name)
    body = HDR[HDR.index("{", start) + 1:end.start()]
    body = re.sub(r"/\*.*?\*/", " ", body, flags=re.S)
    body = re.sub(r"//[^\n]*", " ", body)
    n = 0
    for decl in body.split(";"):
        decl = decl.strip()
        if not decl:
            continue
        # "u16 w, h" is two fields; "const u8* lead" is one.
        n += len([x for x in decl.split(",") if x.strip()])
    return n


sys.path.insert(0, os.path.join(
    ROOT, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"))
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="gbart-home-"))
os.makedirs(os.path.join(os.environ["NB_HOME"], ".config", "notebook"),
            exist_ok=True)
import gbabuild                                             # noqa: E402

# A project exercising every kind, so every initialiser is emitted.
_full = {
    "name": "All", "scripts": [],
    "sprites": [{"id": "spr", "w": 16, "h": 16, "ox": 8, "oy": 8,
                 "anim_speed": 0, "frames": [[0] * 256]}],
    "tilesets": [{"id": "ts", "size": 8, "solid": [True],
                  "tiles": [[0] * 64]}],
    "sounds": [{"id": "snd", "tempo": 6, "loop": True, "steps": 4,
                "lead": [60, 0, 0, 0], "bass": [0] * 4,
                "drum": [1, 0, 0, 0], "kind": 1, "duty": 2, "vol": 9,
                "decay": 3}],
    "objects": [{"id": "obj", "sprite": "spr", "visible": True, "solid": True,
                 "tilecol": 1, "depth": 2, "bb_inset": 3,
                 "events": [{"type": "step", "actions": []}]}],
    "rooms": [{"id": "rm", "w": 240, "h": 160, "bg": "#000000",
               "instances": [{"object": "obj", "x": 8, "y": 8}],
               "tiles": [0] * (30 * 20), "far": [1] * 1024, "far_div": 3,
               "edge_open": True,
               "warps": [{"x": 0, "y": 0, "w": 8, "h": 8, "room": "rm2",
                          "tx": 4, "ty": 4}]},
              {"id": "rm2", "w": 240, "h": 160, "bg": "#000000",
               "instances": [], "tiles": None}],
    "start_room": "rm"}
_c = gbabuild.generate_c(_full)


def emitted_fields(array_decl):
    """Values in the FIRST initialiser of a generated array."""
    m = re.search(re.escape(array_decl) + r"[^=]*=\s*\{(.*?)\};", _c, re.S)
    if not m:
        return None
    first = re.search(r"\{([^{}]*)\}", m.group(1))
    if not first:
        return None
    return len([f for f in first.group(1).split(",") if f.strip()])


for _struct, _array in (("nb_Sprite", "nb_Sprite nb_sprites[]"),
                        ("nb_Object", "nb_Object nb_objects[]"),
                        ("nb_Room", "nb_Room nb_rooms[]"),
                        ("nb_Sound", "nb_Sound nb_sounds[]"),
                        ("nb_Warp", "nb_Warp room_0_warps[]"),
                        ("nb_InstanceDef", "nb_InstanceDef room_0_insts[]")):
    _want = struct_fields(_struct)
    _got = emitted_fields(_array)
    ok(_got == _want,
       "%s: the generator emits all %d fields" % (_struct, _want),
       "declares %d, emits %s -- the missing ones are zero-filled, so the "
       "feature they carry is silently absent" % (_want, _got))

print("\n== the generator emits what the runtime reads ==")
sys.path.insert(0, os.path.join(
    ROOT, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"))
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="gbart-home-"))
os.makedirs(os.path.join(os.environ["NB_HOME"], ".config", "notebook"),
            exist_ok=True)
import gbabuild                                             # noqa: E402

proj = {"name": "T", "sounds": [], "sprites": [], "objects": [],
        "scripts": [],
        "tilesets": [{"id": "ts", "size": 16, "solid": [False, True],
                      "tiles": [[0] * 256, [0] * 256]}],
        "rooms": [{"id": "rm", "w": 240, "h": 160, "bg": "#000000",
                   "instances": [], "tiles": [0] * (30 * 20),
                   "far": [1] * 1024, "far_div": 4, "edge_open": True}],
        "start_room": "rm"}
c = gbabuild.generate_c(proj)
room = re.search(r"const nb_Room nb_rooms\[\].*?};", c, re.S).group(0)
ok("nb_tile_solid" in room, "the room points at the solid table")
ok("room_0_far" in room, "...and at its parallax layer")
# Field POSITION, not the end of the line: nb_Room grows by appending, so an
# assertion anchored to the trailing text breaks every time a field is added
# and says nothing about the fields it claims to check.
_fields = [f.strip() for f in
           re.search(r"\{([^{}]*)\},", room).group(1).split(",")]
ok(len(_fields) >= 13, "the room initialiser carries every field",
   "%d fields: %s" % (len(_fields), _fields))
ok(_fields[9] == "4", "...far_div among them", str(_fields[9:11]))
ok(_fields[10] == "1", "...and edge_open", str(_fields[9:11]))

m = re.search(r"const u8 nb_tile_solid\[\] = \{ ([^}]*)\};", c)
ok(m is not None, "the solid table is emitted")
if m:
    flags = [int(x) for x in m.group(1).split(",")]
    # A 16x16 tile is FOUR 8x8 cells and the room tilemap addresses cells, so
    # one solid tile must mark four of them. Marking one is the off-by-a-factor
    # that makes three quarters of every wall passable.
    ok(sum(flags) == 4,
       "a solid 16x16 tile marks all four of its cells", str(flags))
    ok(flags[0] == 0, "the blank tile at index 0 is never solid")

# No tile marked solid must leave the pointer null rather than emit a table of
# zeroes the runtime would walk for nothing.
plain = dict(proj)
plain["tilesets"] = [{"id": "ts", "size": 8, "solid": [False],
                      "tiles": [[0] * 64]}]
c2 = gbabuild.generate_c(plain)
room2 = re.search(r"const nb_Room nb_rooms\[\].*?};", c2, re.S).group(0)
ok("nb_tile_solid" not in room2,
   "a project with no solid tiles does not point at a table of zeroes")

# The two halves of tile collision fail INDEPENDENTLY: the room must point at a
# solid table, AND the object must be marked as consulting it. Emitting the
# table alone changed nothing, because runtime.c returns early on tilecol == 0
# and moves the instance without looking at the tile layer.
mover = extract("inst_move")
ok("if (!ob->tilecol)" in mover,
   "the runtime still short-circuits on tilecol",
   "runtime.c changed shape; this pairing may no longer be the seam")
obj_row = re.search(r"const nb_Object nb_objects\[\].*?};", _c, re.S).group(0)
first_obj = [f.strip() for f in re.search(r"\{([^{}]*)\}", obj_row).group(1).split(",")]
ok(first_obj[8] == "1",
   "an object marked as colliding emits a non-zero tilecol",
   "tilecol=%s -- the solid table would be built and never consulted"
   % first_obj[8])
ok(first_obj[7] == "2", "...its drawing depth", str(first_obj[7]))
ok(first_obj[9:13] == ["3"] * 4,
   "...and one inset reaches all four sides of the box", str(first_obj[9:13]))
# An object that says nothing must keep the behaviour it had before these
# fields existed, or every project made before tonight changes how it moves.
_second = re.findall(r"\{([^{}]*)\}", obj_row)
if len(_second) > 1:
    _sf = [f.strip() for f in _second[1].split(",")]
    ok(_sf[8] == "0", "an object that says nothing still moves freely",
       str(_sf))

# And the shipped example must demonstrate it rather than merely contain a
# picture of a wall.
import gbasdk                                               # noqa: E402
ex = gbabuild.generate_c(gbasdk.GbaSdk._example_project(None))
ex_obj = re.search(r"const nb_Object nb_objects\[\].*?};", ex, re.S).group(0)
ex_first = [f.strip() for f in
            re.search(r"\{([^{}]*)\}", ex_obj).group(1).split(",")]
ok(ex_first[8] != "0",
   "the example game's player is stopped by its own walls", str(ex_first))

# ---- a number too big for the hardware is clamped, not wrapped ----
# Text that is not a number already became 0. A number too LARGE was passed
# through verbatim into an s32, where it wraps silently — no compiler warning,
# nothing in the build report, just a score that comes out wrong.
ok(gbabuild._int("999999999999") == gbabuild.S32_MAX,
   "a value past the top of an s32 is clamped", gbabuild._int("999999999999"))
ok(gbabuild._int("-999999999999") == gbabuild.S32_MIN,
   "and past the bottom", gbabuild._int("-999999999999"))
ok(gbabuild._int("2147483647") == 2147483647 and gbabuild._int("-3") == -3
   and gbabuild._int("abc", 7) == 7,
   "ordinary values and the not-a-number default are unchanged",
   (gbabuild._int("2147483647"), gbabuild._int("-3"), gbabuild._int("abc", 7)))

# ---- distance, direction and the random number generator ----
# Three pieces of pure arithmetic that everything else leans on: rt_dist_to and
# rt_dir_to are how chase, nearest and every "move toward" behave, and rt_random
# is every bit of variety in a game. None of the three was exercised — they are
# not hardware, so nothing stopped them being run on the host except nobody
# having done it.
_atan = re.search(r"static const u8 nb_atan_q\[[^\]]*\]\s*=\s*\{.*?\};",
                  open(SRC, encoding="utf-8").read(), re.S).group(0)
_math = """#include <stdio.h>
#include <math.h>
typedef signed int s32; typedef unsigned int u32;
typedef unsigned short u16; typedef unsigned char u8; typedef signed short s16;
static u32 g_rng = 1;
%s
%s
%s
%s
%s
int main(void){
  int worst = 0, a;
  for (a = 0; a < 256; a++) {
    double r = a * 3.14159265358979 / 128.0;
    int dx = (int)(1000 * cos(r)), dy = -(int)(1000 * sin(r));
    int got = dir_of(dx, dy), e1 = (got - a) & 255, e2 = (a - got) & 255;
    int e = e1 < e2 ? e1 : e2;
    if (e > worst) worst = e;
  }
  printf("worst %%d\\n", worst);
  {
    struct { int dx, dy, want; } t[] = {
      { 1, 0, 0 }, { 0, -1, 64 }, { -1, 0, 128 }, { 0, 1, 192 },
      { 1, -1, 32 }, { -1, -1, 96 }, { -1, 1, 160 }, { 1, 1, 224 },
      { 0, 0, 0 } };
    int bad = 0; unsigned i;
    for (i = 0; i < sizeof t / sizeof t[0]; i++)
      if (dir_of(t[i].dx, t[i].dy) != t[i].want) bad++;
    printf("exact %%d\\n", bad);
  }
  printf("hyp %%d %%d %%d %%d\\n", hypot_i(3,4), hypot_i(0,0),
         hypot_i(-3,-4), hypot_i(0,7));
  rt_random_seed(1);
  int seen[64]; for (a = 0; a < 64; a++) seen[a] = 0;
  for (a = 0; a < 4000; a++) seen[rt_random(64)]++;
  int lo = 4000, hi = 0;
  for (a = 0; a < 64; a++) { if (seen[a] < lo) lo = seen[a];
                             if (seen[a] > hi) hi = seen[a]; }
  printf("rng %%d %%d %%d\\n", lo, hi, rt_random(0));
  rt_random_seed(7); int f = rt_random(1000);
  rt_random_seed(7); printf("seed %%d\\n", f == rt_random(1000));
  return 0; }
""" % (_atan, extract("isqrt32"), extract("hypot_i"), extract("dir_of"),
       extract("rt_random") + "\n" + extract("rt_random_seed"))
_out = run_c(_math, "distance, direction and random")
_v = dict(ln.split(None, 1) for ln in _out.strip().split("\n") if ln.strip())
# The eight directions a player can hold are exact; that is the assertion that
# matters. Sweeping the whole circle can only be within ONE step, because the
# test's own inputs are integers — `(int)(1000*cos t)` does not sit exactly on
# the ray for most angles, so a worst error of 1/256 is the harness rounding,
# not the function. Asserting 0 there was wrong about the test, not the code.
ok(_v.get("exact") == "0",
   "the eight cardinal and diagonal directions are exact", _v.get("exact"))
ok(_v.get("worst") in ("0", "1"),
   "...and no angle in the circle is off by more than one step of 256",
   _v.get("worst"))
ok(_v.get("hyp", "").split() == ["5", "0", "5", "7"],
   "distance is exact for a 3-4-5, for nothing, for negatives and along an axis",
   _v.get("hyp"))
_rng = (_v.get("rng") or "0 0 0").split()
ok(int(_rng[0]) > 0, "every value in the range comes up", _v.get("rng"))
ok(int(_rng[1]) < int(_rng[0]) * 3,
   "...and none of them dominates", _v.get("rng"))
ok(_rng[2] == "0", "a range of zero returns zero rather than dividing by it",
   _v.get("rng"))
ok(_v.get("seed") == "1", "the same seed gives the same sequence", _v.get("seed"))

# ---- the alarm count the generator believes matches the runtime's ----
# `alarm[9] = 30`, written by anyone who assumes there are ten, emitted a write
# past the end of the array into the variable slots that follow it in the
# Instance struct. The generator rejects an out-of-range literal now, which
# means it carries a copy of the runtime's NB_MAX_ALARMS — so the copy has to
# be held to the original.
_hdr = open(os.path.join(RT, "runtime.h"), encoding="utf-8").read()
_m = re.search(r"#define\s+NB_MAX_ALARMS\s+(\d+)", _hdr)
ok(_m is not None and int(_m.group(1)) == gbabuild.MAX_ALARMS,
   "gbabuild.MAX_ALARMS matches NB_MAX_ALARMS in the runtime header",
   "header %s, generator %s" % (_m.group(1) if _m else "?",
                                gbabuild.MAX_ALARMS))

# ---- an author's column name cannot be a C keyword ----
# "char" is what a character table's first column gets called, and it emitted
# `char char;` — "two or more data types in declaration", pointing at a line of
# generated code the author has never seen.
for _kw in ("char", "short", "double", "int", "return", "struct"):
    ok(gbabuild._Gen._c_ident(_kw, "col0") not in gbabuild.C_KEYWORDS,
       "a column named %r does not emit a C keyword" % _kw,
       gbabuild._Gen._c_ident(_kw, "col0"))
ok(gbabuild._Gen._c_ident("Base HP", "col0") == "Base_HP",
   "an ordinary column name is left alone",
   gbabuild._Gen._c_ident("Base HP", "col0"))
# and the escape hatch must not collide with a column already using it
_kwproj = {"name": "K", "sprites": [], "sounds": [], "objects": [],
           "start_room": "rm_a", "tilesets": [],
           "tables": [{"id": "tbl_1", "name": "stats",
                       "columns": [{"name": "char", "type": "int"},
                                   {"name": "char_", "type": "int"}],
                       "rows": [["1", "2"]]}],
           "rooms": [{"id": "rm_a", "w": 240, "h": 160, "speed": 60,
                      "bg": "#101820", "instances": [], "warps": [],
                      "tiles": [0] * 600}]}
_flds = re.findall(r"^    \w+ (\w+);", gbabuild.generate_c(_kwproj), re.M)
ok(len(_flds) == len(set(_flds)),
   "renaming around a keyword does not collide with a real column", str(_flds))

# ---- author text survives the trip into a C string literal ----
# Decoded by C's rules rather than by re-implementing gbabuild's escaper: the
# test asks "does a compiler read back what the author typed", which is the
# property that matters and stays true whichever escape form is chosen.
def _c_decode(lit):
    assert lit[0] == '"' and lit[-1] == '"', lit
    body, out, i = lit[1:-1], [], 0
    while i < len(body):
        ch = body[i]
        if ch != "\\":
            out.append(ord(ch)); i += 1; continue
        i += 1
        e = body[i]
        if e == "x":                       # greedy: C consumes EVERY hex digit
            i += 1
            j = i
            while j < len(body) and body[j] in "0123456789abcdefABCDEF":
                j += 1
            out.append(int(body[i:j], 16) & 0xFF)   # over-range wraps/errors
            if j - i > 2:
                out.append(0xFFF)          # marker: the escape swallowed text
            i = j
        elif e in "01234567":              # octal: at most THREE digits
            j = i
            while j < len(body) and j - i < 3 and body[j] in "01234567":
                j += 1
            out.append(int(body[i:j], 8) & 0xFF)
            i = j
        else:
            out.append({"n": 10, "t": 9, "\\": 92, '"': 34}.get(e, ord(e)))
            i += 1
    return bytes(b & 0xFF for b in out if b != 0xFFF), (0xFFF in out)

for _s in ("caf\u00e92", "\u00fcber9", "caf\u00e9", "na\u00efve1",
           'a\\b"c', "tab\there", "line\nbreak", "100%", "plain"):
    _dec, _ate = _c_decode(gbabuild._cstr(_s))
    _want = _s.encode("latin-1", "replace")
    ok(_dec == _want and not _ate,
       "a C literal reads back as the author typed it: %r" % _s,
       "got %r, want %r%s" % (_dec, _want, " (escape swallowed text)" if _ate else ""))

# ---- the generator must not mint a name the runtime already owns ----
# A table's C name comes from what the author typed, prefixed nb_ — so a table
# called "score" emitted nb_score, which runtime.h declares, and the build died
# with "conflicting types for 'nb_score'" pointing into a header the author has
# never seen. gbabuild.RESERVED_C holds the runtime's names so the generator
# renames around them; this check is what stops that list drifting behind the
# runtime it describes.
_rt_names = set()
for _f in ("runtime.h", "runtime.c", "gba.h"):
    _p = os.path.join(RT, _f)
    if os.path.exists(_p):
        with open(_p, encoding="utf-8") as _fh:
            _rt_names |= set(re.findall(r"\bnb_[A-Za-z_0-9]+", _fh.read()))
_missing = sorted(_rt_names - set(gbabuild.RESERVED_C))
ok(not _missing,
   "every nb_ name the runtime owns is reserved against generated code",
   "not in RESERVED_C: " + ", ".join(_missing))

# and the reservation has to actually change the emitted symbol
_collide = {"name": "T", "sprites": [], "sounds": [], "objects": [],
            "start_room": "rm_a", "tilesets": [],
            "tables": [{"id": "tbl_1", "name": "score",
                        "columns": [{"name": "v", "type": "int"}],
                        "rows": [["1"]]}],
            "rooms": [{"id": "rm_a", "w": 240, "h": 160, "speed": 60,
                       "bg": "#101820", "instances": [], "warps": [],
                       "tiles": [0] * 600}]}
_c = gbabuild.generate_c(_collide)
_m = re.search(r"const nb_row_\w+ (nb_\w+)\[\]", _c)
ok(_m is not None and _m.group(1) != "nb_score",
   "a table named after a runtime global is renamed, not left to collide",
   _m.group(1) if _m else "no table emitted")


# ---------------------------------------------------------------------------
# Collision: the one piece of arithmetic every game leans on.
# ---------------------------------------------------------------------------
# rt_meeting and rt_place_meeting are the SAME loop written twice, differing
# only in where the box comes from. Duplicated code that must agree is exactly
# what a test should pin, because an edit to one is invisible in the other.
print("\n== collision: boxes, edges, and the two copies of the loop ==")

_COLL_TYPES = HOST_TYPES + """
#define NB_MAX_INSTANCES 128
#define NB_MAX_ALARMS 4
#define NB_MAX_VARS 12
typedef struct { u16 w,h; s16 ox,oy;
                 u16 nframes,tile,tiles_per_frame,shape,size,palbank,anim_speed;
               } nb_Sprite;
typedef struct Instance Instance;
typedef void (*nb_event_fn)(Instance*);
typedef struct { s16 sprite; u8 visible, solid;
                 nb_event_fn create, step, draw, destroy;
                 u8 depth, tilecol, bb_l, bb_t, bb_r, bb_b; } nb_Object;
struct Instance { u8 active; s16 object, sprite, image_index, image_speed,
                  image_accum; s32 x,y,hspeed,vspeed,grav;
                  s32 alarm[NB_MAX_ALARMS]; s32 var[NB_MAX_VARS];
                  s16 hspd8,vspd8,grav8,xsub,ysub;
                  u8 hidden,flip,depth,flags,angle; s16 scale;
                  u8 anim_lo,anim_hi; s16 gx,gy; u16 glide; };
static Instance g_inst[NB_MAX_INSTANCES];
static Instance* g_other = 0;
static nb_Sprite nb_sprites[8];
static nb_Object nb_objects[8];
static int nb_object_count = 8;
static int nb_sprite_count = 8;
"""

_bbox_src = extract("rt_bbox_at")
ok("if (*r < *l) *r = *l;" in _bbox_src,
   "the extracted box code still collapses a crossed inset pair",
   "runtime.c changed shape; this test may be measuring something else")

_prog = _COLL_TYPES + _bbox_src + "\n" + extract("rt_bbox") + "\n" \
    + extract("rt_meeting") + "\n" + extract("rt_place_meeting") + r"""
static Instance* mk(int slot, int obj, int spr, s32 x, s32 y) {
    Instance* in = &g_inst[slot];
    memset(in, 0, sizeof *in);
    in->active = 1; in->object = (s16)obj; in->sprite = (s16)spr;
    in->x = x; in->y = y;
    return in;
}
static int fails = 0;
static void T(int cond, const char* what) {
    if (!cond) { printf("failed: %s\n", what); fails++; }
}
int main(void) {
    for (int i = 0; i < 8; i++) {
        nb_sprites[i].w = 16; nb_sprites[i].h = 16;
        nb_objects[i].sprite = (s16)i;
    }
    memset(g_inst, 0, sizeof g_inst);
    Instance* a = mk(0, 0, 0, 0, 0);
    Instance* b = mk(1, 1, 1, 0, 0);

    /* Two 16px boxes: the first gap at which they come apart. */
    int first_clear = -1;
    for (s32 dx = 0; dx <= 20; dx++) {
        b->x = dx;
        if (!rt_meeting(a, -1)) { first_clear = (int)dx; break; }
    }
    printf("first_clear=%d\n", first_clear);

    /* Symmetry, and the size of the overlap region, over every offset. */
    int asym = 0, hits = 0, disagree = 0;
    for (s32 dx = -20; dx <= 20; dx++)
      for (s32 dy = -20; dy <= 20; dy++) {
        b->x = dx; b->y = dy;
        int ab = rt_meeting(a, -1) != 0;
        int ba = rt_meeting(b, -1) != 0;
        int pm = rt_place_meeting(a, a->x, a->y, -1) != 0;
        if (ab != ba) asym++;
        if (ab != pm) disagree++;
        hits += ab;
      }
    printf("asym=%d hits=%d disagree=%d\n", asym, hits, disagree);

    /* The origin offset moves the box with the artwork. */
    memset(g_inst, 0, sizeof g_inst);
    a = mk(0, 0, 0, 100, 100); b = mk(1, 1, 1, 100, 100);
    nb_sprites[0].ox = 8; nb_sprites[0].oy = 8;
    b->x = 108; T(!rt_meeting(a, -1), "a centred origin is clear at 108");
    b->x = 107; T(rt_meeting(a, -1) != 0, "a centred origin touches at 107");
    nb_sprites[0].ox = nb_sprites[0].oy = 0;

    /* An inset really shrinks the box. */
    memset(g_inst, 0, sizeof g_inst);
    a = mk(0, 0, 0, 0, 0); b = mk(1, 1, 1, 15, 0);
    T(rt_meeting(a, -1) != 0, "15 apart, no inset: touching");
    nb_objects[0].bb_r = 4;
    T(!rt_meeting(a, -1), "a 4px right inset opens that gap");
    b->x = 11;
    T(rt_meeting(a, -1) != 0, "and 11 closes it again");
    nb_objects[0].bb_r = 0;

    /* Every inset pair the generator can emit (it clamps each to 0..64) on
       every sprite size the hardware has. The box must stay inside its own
       sprite: a box displaced clear of its artwork would collide with what
       the instance is nowhere near and pass through what it is touching. */
    s32 l, t, r, bo;
    int outside = 0, collapsed = 0;
    const int dim[4] = {8, 16, 32, 64};
    for (int sz = 0; sz < 4; sz++) {
        nb_sprites[0].w = nb_sprites[0].h = (u16)dim[sz];
        for (int bl = 0; bl <= 64; bl++)
          for (int br = 0; br <= 64; br++) {
            nb_objects[0].bb_l = (u8)bl; nb_objects[0].bb_r = (u8)br;
            rt_bbox(a, &l, &t, &r, &bo);
            if (r < l || l < a->x || r > a->x + dim[sz] - 1) outside = 1;
            if (bl + br >= dim[sz]) collapsed++;
          }
    }
    printf("outside=%d collapsed=%d\n", outside, collapsed);
    nb_sprites[0].w = nb_sprites[0].h = 16;
    nb_objects[0].bb_l = nb_objects[0].bb_r = 0;

    /* Filtering, self, inactive, the published hit, and a null self. */
    memset(g_inst, 0, sizeof g_inst);
    a = mk(0, 0, 0, 0, 0); b = mk(1, 1, 1, 0, 0);
    T(rt_meeting(a, 1) != 0, "an object filter finds that object");
    T(rt_meeting(a, 2) == 0, "an object filter rejects the others");
    T(rt_meeting(a, 0) == 0, "an instance never meets itself");
    T(rt_place_meeting(a, 0, 0, 0) == 0, "nor by place_meeting");
    b->active = 0;
    T(rt_meeting(a, -1) == 0, "a destroyed instance is not collidable");
    b->active = 1;
    g_other = 0;
    T(rt_meeting(a, -1) != 0 && g_other == b, "the hit is published in other");
    T(rt_meeting(0, -1) == 0, "a null self returns nothing rather than faulting");
    T(rt_place_meeting(0, 0, 0, -1) == 0, "nor does place_meeting fault");
    printf("fails=%d\n", fails);
    return 0;
}
"""
_out = run_c(_prog, "collision")
_c = dict(kv.split("=", 1) for ln in _out.splitlines() if "=" in ln
          for kv in ln.split() if "=" in kv)

ok(_c.get("first_clear") == "16",
   "two 16px boxes come apart at exactly 16px, not 15 or 17",
   _c.get("first_clear"))
# 961 is not a magic number: two 16px boxes overlap for offsets -15..15 on each
# axis, so 31 x 31. Asserting the COUNT rather than "some hits" is what makes
# this a measurement of the box maths instead of a check that it runs.
ok(_c.get("hits") == "961",
   "the overlap region is exactly the 31x31 offsets geometry predicts",
   _c.get("hits"))
# The invariant, not the answer. When one edge of the overlap test was made
# exclusive, first_clear still read 16 -- the break was on the far side, where
# the test that knew the expected answer was not looking. This caught it.
ok(_c.get("asym") == "0",
   "if A meets B then B meets A, at every offset",
   _c.get("asym"))
ok(_c.get("disagree") == "0",
   "the two copies of the collision loop agree at every offset",
   _c.get("disagree"))
ok(_c.get("outside") == "0",
   "no inset pair can push a hit box outside the sprite it belongs to",
   _c.get("outside"))
ok(int(_c.get("collapsed") or 0) > 1000,
   "and the crossed-inset path is reached, not merely compiled",
   _c.get("collapsed"))
ok(_c.get("fails") == "0",
   "origins, filters, inactive instances and a null self all behave",
   "%s case(s) failed -- see the lines above" % _c.get("fails"))

# ---------------------------------------------------------------------------
# Every number a player sees goes through int_to_str, into a 12-byte buffer.
# ---------------------------------------------------------------------------
print("\n== int_to_str: the score read-out, and the buffer behind it ==")
_prog = HOST_TYPES + extract("int_to_str") + r"""
/* Canaries either side, so a write past buf[11] is visible here rather than
   landing silently in whatever the compiler happened to put next to it -- on
   the console that is a stack smash with no diagnostic at all. */
static char pre[8], buf[12], post[8];
static int bad = 0;
static void check(s32 v, int digits, const char* want) {
    memset(pre, '#', 8); memset(post, '#', 8); memset(buf, '?', 12);
    int n = int_to_str(buf, v, digits);
    for (int i = 0; i < 8; i++)
        if (pre[i] != '#' || post[i] != '#') {
            printf("smashed by %d,%d\n", v, digits); bad++; break;
        }
    if (strcmp(buf, want)) {
        printf("wrote \"%s\" for %d,%d -- wanted \"%s\"\n", buf, v, digits, want);
        bad++;
    }
    if (n != (int)strlen(want)) { printf("length %d,%d\n", v, digits); bad++; }
}
int main(void) {
    check(0, 0, "0");
    check(-7, 0, "-7");
    check(2147483647, 0, "2147483647");
    /* The most negative s32 is the classic one: negating it overflows, and
       every digit of it plus a sign is the widest this can ever print. */
    check((s32)0x80000000, 0, "-2147483648");
    check(0, 10, "0000000000");
    check(-1, 10, "-0000000001");
    check((s32)0x80000000, 10, "-2147483648");
    check(1234567890, 10, "1234567890");
    int widest = 0;
    const s32 probe[6] = {0, 1, -1, 2147483647, (s32)0x80000000, -999999999};
    for (int d = 0; d <= 10; d++)
        for (int k = 0; k < 6; k++) {
            memset(buf, '?', 12);
            int n = int_to_str(buf, probe[k], d);
            if (n > widest) widest = n;
        }
    printf("bad=%d widest=%d cap=%d\n", bad, widest, (int)sizeof buf);
    return 0;
}
"""
_out = run_c(_prog, "int_to_str")
_v = dict(kv.split("=", 1) for kv in _out.split() if "=" in kv)
ok(_v.get("bad") == "0",
   "every number a game can display prints correctly, sign and padding",
   "%s case(s) wrong -- see above" % _v.get("bad"))
# rt_draw_int_pad caps digits at 10, and the widest value carries a sign, so
# the worst case is 11 characters and a terminator. The buffer is 12: correct,
# with nothing to spare. Anything that widens either has to grow the buffer.
ok(_v.get("widest") == "11" and _v.get("cap") == "12",
   "the widest output is 11 characters and the buffer is exactly 12",
   "widest %s into %s bytes" % (_v.get("widest"), _v.get("cap")))


# ---------------------------------------------------------------------------
# The instance pool, and what happens when a bullet pattern exhausts it.
# ---------------------------------------------------------------------------
# rt_create returns 0 when 128 instances are already live, and EVERY caller in
# the generated code discards that -- the room loader and the create_instance
# action both. A game that spawns past the cap simply stops spawning, with no
# diagnostic anywhere. The refusals are counted now and shown on the profiler
# overlay; this pins the count so it cannot quietly stop being kept.
print("\n== the instance pool refuses, and says how often ==")
_prog = _COLL_TYPES.replace("static Instance* g_other = 0;",
                            "static Instance* g_other = 0;\n"
                            "static u32 g_create_refused = 0;\n"
                            "#define NB_MAX_DEPTH 8\n") \
    + extract("rt_create") + r"""
int main(void) {
    for (int i = 0; i < 8; i++) nb_objects[i].sprite = (s16)i;
    memset(g_inst, 0, sizeof g_inst);
    int made = 0;
    for (int i = 0; i < 200; i++)
        if (rt_create(0, i, i)) made++;
    printf("made=%d refused=%u cap=%d\n", made, g_create_refused,
           NB_MAX_INSTANCES);
    /* Freeing one slot must let exactly one more in: the pool has to be
       reusable, or a bullet-hell dies after its first 128 bullets ever. */
    g_inst[7].active = 0;
    u32 before = g_create_refused;
    int again = rt_create(0, 1, 1) ? 1 : 0;
    int more  = rt_create(0, 2, 2) ? 1 : 0;
    printf("reuse=%d then=%d newrefusals=%u\n", again, more,
           g_create_refused - before);
    /* An out-of-range object is a different failure and must not be counted
       as the pool being full -- that would send an author hunting a capacity
       problem that is really a bad object id. */
    before = g_create_refused;
    int bad = rt_create(99, 0, 0) ? 1 : 0;
    printf("bad=%d badrefusals=%u\n", bad, g_create_refused - before);
    return 0;
}
"""
_out = run_c(_prog, "instance pool")
_v = dict(kv.split("=", 1) for kv in _out.split() if "=" in kv)
ok(_v.get("made") == "128" and _v.get("cap") == "128",
   "the pool fills to exactly its cap and no further",
   "made %s of a %s cap" % (_v.get("made"), _v.get("cap")))
ok(_v.get("refused") == "72",
   "and every create past it is counted, not silently dropped",
   "refused %s of the 72 attempts past the cap" % _v.get("refused"))
ok(_v.get("reuse") == "1" and _v.get("then") == "0"
   and _v.get("newrefusals") == "1",
   "a freed slot admits exactly one more instance",
   "reuse=%s then=%s refusals=%s" % (_v.get("reuse"), _v.get("then"),
                                     _v.get("newrefusals")))
ok(_v.get("bad") == "0" and _v.get("badrefusals") == "0",
   "a bad object id is rejected without being blamed on the pool",
   "bad=%s counted=%s" % (_v.get("bad"), _v.get("badrefusals")))

print("\n%s  (%d failed)" % ("FAILURES: " + ", ".join(FAIL) if FAIL
                             else "all checks pass", len(FAIL)))
sys.exit(1 if FAIL else 0)
