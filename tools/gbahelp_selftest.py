#!/usr/bin/env python3
"""
gbahelp_selftest — the reference and the course in applied C.

What this has to prove, beyond "it imports":

1. The DERIVED half is actually derived. The reference claims to come from
   ACTION_DEFS, runtime.h and gba.h rather than from a hand-written copy. If the
   parsers silently return nothing the pane still opens, still looks finished,
   and is empty of the thing it exists for -- so the counts are asserted, not
   the absence of an exception.

2. Every checkpoint can go BOTH ways. A checkpoint that can only report "not
   done" is worse than no checkpoint: the course reads as unfinishable and the
   author has no way to tell a bug from their own work. Each check is therefore
   run against a project that must fail it AND a project that must pass it.
   (Fourth time this project has been bitten by a gate that could not go red;
   here the risk is the mirror -- a gate that cannot go green.)

3. Coverage. A reference of bare signatures is not documentation. Every engine
   call must carry a description from somewhere.

4. Nothing in the content violates the OS text mandate.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, DE)

os.environ.setdefault("GDK_BACKEND", "x11")
import gbahelp                                              # noqa: E402
import gbabuild                                             # noqa: E402

FAIL = []


def ok(cond, label, detail=""):
    print("  %s %s%s" % ("PASS" if cond else "FAIL", label,
                         ("  -- " + detail) if detail and not cond else ""))
    if not cond:
        FAIL.append(label)


print("\n== derived reference ==")
topics = gbahelp.all_topics()
by_section = {}
for t in topics:
    by_section.setdefault(t.section, []).append(t)

ok(len(by_section.get("Course", [])) >= 16, "course has its lessons",
   str(len(by_section.get("Course", []))))
ok(len(by_section.get("Actions", [])) >= 8, "action groups derived",
   str(len(by_section.get("Actions", []))))

eng = gbahelp.reference_engine()
calls = [v for t in eng for k, v in t.body if k == "code"]
ok(len(calls) >= 80, "engine calls parsed from runtime.h", str(len(calls)))
ok(any("rt_hdma_start" in c for c in calls), "phase 6 DMA reached the reference")
ok(any("rt_irq_set" in c for c in calls), "phase 6 interrupts reached the reference")
ok(any("rt_timer_start" in c for c in calls), "phase 6 timers reached the reference")

regs = gbahelp.reference_registers()
names = [r[0] for t in regs for k, v in t.body if k == "table" for r in v[1]]
ok(len(names) >= 100, "hardware names parsed from gba.h", str(len(names)))
for want in ("REG_DISPCNT", "REG_IE", "REG_TM0CNT_L", "REG_DMA1SAD", "REG_FIFO_A"):
    ok(want in names, "gba.h name present: " + want)

# A parser that returns nothing must not look like a reference that is merely
# short: assert the failure mode explicitly.
ok(gbahelp.reference_engine("/nonexistent/runtime.h") == [],
   "a missing header yields no topics rather than an exception")

print("\n== coverage: does the PARSER see everything the header declares? ==")
# WHAT THIS PROVES, EXACTLY. The reference is DERIVED from runtime.h, so
# comparing the two cannot catch a call somebody forgot to document -- adding a
# declaration adds a reference entry by construction. Verified: a bare new
# declaration leaves this section green.
#
# What it does catch is the PARSER failing to see something the header
# declares, which is a silent hole in the only documentation on the machine.
# That is not hypothetical: a declaration wrapped across two lines matched
# nothing when the header was read a line at a time, and rt_menu_open_var was
# declared, implemented, used by an action, and absent from the reference.
# Reverting that fix turns this section red, which is how it was checked.
#
# Whether a call has a DESCRIPTION is a separate question, and the section
# below answers it -- that one reads hand-written comments, so it can and does
# fail.
_RT = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay/"
                         "opt/notebook/gbaruntime")
_hdr = open(os.path.join(_RT, "runtime.h"), encoding="utf-8").read()
_gba = open(os.path.join(_RT, "gba.h"), encoding="utf-8").read()

_declared = sorted(set(re.findall(r"\b(rt_[A-Za-z0-9_]+)\s*\(", _hdr)))
_refeng = "\n".join(t.text() for t in gbahelp.reference_engine())
# Against the REFERENCE, not the whole book: a name that happens to appear in a
# guide's prose is not the same as being documented with its signature.
_gone = [c for c in _declared if c + "(" not in _refeng]
ok(not _gone, "every engine call in runtime.h is in the reference",
   "%d missing: %s" % (len(_gone), _gone[:6]))
ok(len(_declared) > 120, "...and there are a lot of them", str(len(_declared)))

# The specific shape that hid one: a declaration wrapped across lines matches
# nothing when the header is read a line at a time, and vanishes in silence.
ok("rt_menu_open_var(" in _refeng,
   "a declaration wrapped across lines is still documented")
ok("rt_bg_affine(" in _refeng, "...and so is another one")

_regs = sorted(set(re.findall(r"#define\s+(REG_[A-Za-z0-9_]+)", _gba)))
_refreg = "\n".join(t.text() for t in gbahelp.reference_registers())
_gone_r = [r for r in _regs if r not in _refreg]
ok(not _gone_r, "every hardware register in gba.h is in the reference",
   "%d missing: %s" % (len(_gone_r), _gone_r[:6]))

import gbasdk                                              # noqa: E402
_acts = [a for _g, grp in gbasdk.ACTION_GROUPS for a in grp]
_refact = "\n".join(t.text() for t in gbahelp.reference_actions(
    gbasdk.ACTION_GROUPS, gbasdk.ACTION_DEFS, gbasdk.ACTION_TIPS,
    gbasdk.CONTAINER_ACTIONS,
    presets=gbasdk.ACTION_PRESETS))
_gone_a = [a for a in _acts if gbasdk.ACTION_LABEL[a] not in _refact]
ok(not _gone_a, "every action in the palette is in the reference",
   "%d missing: %s" % (len(_gone_a), _gone_a[:6]))

print("\n== every engine call is documented ==")
# A SECTION BANNER IS NOT A BLANKET. Treating one as covering every call
# beneath it means a call added to an existing section is never flagged --
# verified by adding a bare declaration to the Menus section and watching this
# stay green. The rule that catches it: a call is documented if it has its own
# note OR its section's prose NAMES IT.
#
# 25 calls failed that when it was first applied, all of them accessors whose
# names "say what they do" -- which is exactly the reasoning that leaves a
# reference incomplete. They have notes now.
bare = []
for t in eng:
    intro = " ".join(v for k, v in t.body if k == "p")
    for i, (k, v) in enumerate(t.body):
        if k != "code" or "typedef" in v:
            continue
        name = v.split("(")[0].split()[-1].lstrip("*")
        own = (i + 1 < len(t.body) and t.body[i + 1][0] == "p")
        if not own and name not in intro:
            bare.append(name)
ok(not bare, "every engine call is described, or named by its section",
   "%d undocumented: %s" % (len(bare), bare[:5]))

import re as _re
print("\n== the book only names calls that exist ==")
# The coverage gate above runs one way: every call DECLARED in runtime.h must
# be described. Nothing ran the other way, so prose could name a call that has
# never existed and stay green -- caught by writing "rt_camera_set" into a
# lesson, which is not the camera API and never was. A reader types it and the
# build fails on a name the book gave them.
_decl = set(_re.findall(r"\brt_[a-z0-9_]+", open(
    os.path.join(gbahelp.RUNTIME_DIR, "runtime.h"), encoding="utf-8").read()))
_named = set()
for _t in list(gbahelp.all_topics()) + list(gbahelp.recipe_topics()):
    for _k, _v in _t.body:
        if isinstance(_v, str):
            _named |= set(_re.findall(r"\brt_[a-z0-9_]+", _v))
        elif isinstance(_v, (list, tuple)):       # tables: rows of cells
            for _row in _v:
                for _cell in (_row if isinstance(_row, (list, tuple)) else [_row]):
                    if isinstance(_cell, str):
                        _named |= set(_re.findall(r"\brt_[a-z0-9_]+", _cell))
_ghost = sorted(_named - _decl)
ok(not _ghost, "every engine call the book names is declared in runtime.h",
   "%d invented: %s" % (len(_ghost), _ghost[:5]))

print("\n== nothing taught is undone by the frame loop ==")
# THE BUG THIS EXISTS FOR. rt_flush() ends every VBlank with
# `REG_DISPCNT = g_dispcnt`, and g_dispcnt was a constant fixed at MODE0. The
# "Rotating background" recipe opened with
#     REG_DISPCNT = MODE_1 | BG0_ON | BG2_ON | OBJ_ON | OBJ_1D_MAP;
# which the very next frame overwrote. The recipe compiled, ran, and did
# nothing -- and the affine matrix behind it is CORRECT and has a passing host
# test, because a host test of pure arithmetic cannot see which register the
# frame loop owns.
#
# The general rule: a register the frame loop assigns unconditionally belongs
# to the runtime. Teaching a reader to write it directly is teaching something
# that lasts one frame. Either the runtime offers a call that survives, or the
# reference does not show the register being assigned.
_rtc = os.path.join(os.path.dirname(gbahelp.RUNTIME_DIR.rstrip("/")),
                    "gbaruntime", "runtime.c")
if not os.path.exists(_rtc):
    _rtc = os.path.join(gbahelp.RUNTIME_DIR, "runtime.c")
_flush = ""
_m = _re.search(r"static void rt_flush\(void\)\s*\{", open(_rtc, encoding="utf-8").read())
_src = open(_rtc, encoding="utf-8").read()
if _m:
    _i = _src.index("{", _m.start()); _d = 0; _j = _i
    while _j < len(_src):
        if _src[_j] == "{": _d += 1
        elif _src[_j] == "}":
            _d -= 1
            if _d == 0: break
        _j += 1
    _flush = _src[_i:_j]
ok(bool(_flush), "the frame loop is findable in runtime.c",
   "rt_flush() moved or was renamed; this check is measuring nothing")

# Registers rt_flush assigns outright (not |= or &=): the runtime owns these.
_owned = set(_re.findall(r"\b(REG_[A-Z0-9_]+)\s*=[^=]", _flush))
print("     frame loop owns: %s" % ", ".join(sorted(_owned)))
ok(len(_owned) >= 1, "the frame loop asserts at least one register",
   "found none -- the pattern above probably stopped matching")

# Every code block anywhere in the book, recipes included.
_taught = []
for _t in gbahelp.all_topics():
    for _k, _v in _t.body:
        if _k == "code":
            _taught.append((_t.tid, _v))
for _r in gbahelp.recipe_topics():
    for _k, _v in _r.body:
        if isinstance(_v, str):
            _taught.append((_r.tid, _v))
_clash = []
for _tid, _code in _taught:
    for _reg in _owned:
        if _re.search(r"\b" + _reg + r"\s*=[^=]", _code):
            _clash.append("%s writes %s" % (_tid, _reg))
ok(not _clash,
   "no example assigns a register the frame loop rewrites each VBlank",
   "; ".join(_clash[:4]))

print("\n== checkpoints go both ways ==")
EMPTY = {}
FULL = {"scripts": [{"id": "scr_1", "name": "Maths",
                     "code": "s16 dbl(s16 v) { return v * 2; }\n"
                             "void m(void) { rt_menu_open(0, 1, 2, 3, 4); }"}],
        "tilesets": [{"id": "ts_1", "size": 8, "solid": [False, True],
                      "auto_base": 0,
                      "tiles": [[0] * 64, [0] * 64]}],
        "tables": [{"id": "tbl_1",
                    "columns": [{"name": "Name", "type": "text"}],
                    "rows": [["Bulbasaur"]]}],
        "sounds": [{"id": "snd_1", "steps": 4, "lead": [60, 0, 0, 0],
                    "bass": [0] * 4, "drum": [1, 0, 2, 0],
                    "pcm": [0, 40, 0, -40] * 8}],
        "rooms": [{"id": "rm_1", "w": 240, "h": 160,
                   "warps": [{"x": 0, "y": 0, "w": 8, "h": 8,
                              "room": "rm_2", "tx": 8, "ty": 8}]}],
        "objects": [{"id": "o1", "name": "Hero", "tilecol": 1, "events": [
    {"type": "step", "actions": [
        {"kind": "move_fixed", "dir": "right", "speed": 2},
        {"kind": "say", "text": "Hello.{p}Goodbye."},
        {"kind": "glide", "x": 10, "y": 10, "frames": 30},
        {"kind": "set_var", "var": "hp", "value": 3},
        {"kind": "if_var", "var": "hp", "op": "<", "value": 1, "children": [
            {"kind": "execute_code", "code":
                "s16 t = 0;\n"
                "if (t > 3) { t = 0; }\n"
                "for (int i = 0; i < 4; i++) rt_create(0, 0, 0);\n"
                "REG_BG0HOFS = t;\n"
                "rt_bg_affine(2, 120, 80, 120, 80, t, 256);\n"}]}]}]}]}

for cid in sorted(gbahelp.CHECKS):
    red, _ = gbahelp.run_check(cid, EMPTY)
    green, detail = gbahelp.run_check(cid, FULL)
    ok(red is False, "%s reports not-done on an empty project" % cid)
    ok(green is True, "%s reports done when satisfied" % cid, str(detail))

# Commented-out code must not satisfy a checkpoint: a lesson marked complete by
# text inside /* */ has taught nothing and says otherwise.
COMMENTED = {"objects": [{"id": "o", "events": [{"type": "step", "actions": [
    {"kind": "execute_code",
     "code": "/* if (x) rt_foo(); REG_A = 1; s16 y; for(;;); */"}]}]}]}
for cid in ("code_has_if", "code_calls_engine", "code_writes_register",
            "code_declares_var", "code_has_loop"):
    val, _ = gbahelp.run_check(cid, COMMENTED)
    ok(val is False, "%s ignores commented-out code" % cid)

# ---- a checkpoint must be reachable the way the tool is meant to be used ----
# uses_menu looked only for `rt_menu_open(` in code, so the lesson could be
# finished by writing C and NOT by adding a Show Menu action — the drag-drop
# path this tool exists to offer, and the one its own words promise ("A script
# or action opens a menu"). The action was added after the checkpoint and never
# reached it. Found by asking which content each checkpoint REACTS to, not by
# reading its label.
_menu_action = {
    "name": "M", "sprites": [], "tilesets": [], "sounds": [], "scripts": [],
    "tables": [], "start_room": "rm",
    "objects": [{"id": "o", "events": [{"type": "step", "actions": [
        {"kind": "menu", "var": "choice", "lines": "Fight\nRun"}]}]}],
    "rooms": [{"id": "rm", "w": 240, "h": 160, "instances": [],
               "warps": [], "tiles": []}]}
_menu_code = dict(_menu_action, objects=[{"id": "o", "events": []}],
                  scripts=[{"id": "s",
                            "code": "void f(void){ rt_menu_open(0,0,0,0); }"}])
_menu_none = dict(_menu_action, objects=[{"id": "o", "events": []}])
ok(gbahelp.run_check("uses_menu", _menu_action)[0] is True,
   "a Show Menu ACTION completes the menu lesson",
   str(gbahelp.run_check("uses_menu", _menu_action)))
ok(gbahelp.run_check("uses_menu", _menu_code)[0] is True,
   "and so does rt_menu_open in C",
   str(gbahelp.run_check("uses_menu", _menu_code)))
ok(gbahelp.run_check("uses_menu", _menu_none)[0] is False,
   "and neither leaves it uncompleted",
   str(gbahelp.run_check("uses_menu", _menu_none)))

ok(gbahelp.run_check("no_such_check", FULL)[0] is None,
   "an unknown checkpoint is neither done nor failed")

done, total = gbahelp.course_progress(FULL)
ok(total >= 5, "the course has checkpoints", str(total))
ok(done == total, "a satisfying project completes the course",
   "%d/%d" % (done, total))
ok(gbahelp.course_progress(EMPTY)[0] == 0, "an empty project completes none")

# Every checkpoint the course names must exist, or a lesson shows a step that
# can never be marked done.
named = {v[0] for t in gbahelp.COURSE for k, v in t.body if k == "check"}
ok(named <= set(gbahelp.CHECKS), "every lesson checkpoint is implemented",
   str(sorted(named - set(gbahelp.CHECKS))))

print("\n== the look-down device ==")
obj = {"id": "obj_hero", "name": "Hero", "events": []}
ev = {"type": "step", "actions": [
    {"kind": "move_fixed", "dir": "right", "speed": 2},
    {"kind": "if_var", "var": "hp", "op": "<", "value": 1,
     "children": [{"kind": "destroy_self"}]}]}
code, problems = gbabuild.preview_event_c({"objects": [obj]}, obj, ev)
ok("self->hspeed = 2;" in code, "an action shows the C it produces")
ok("if (self->var[0] < 1)" in code,
   "a variable in an unsaved event is allocated a slot", code)
ok("rt_destroy(self);" in code, "nested actions are shown nested")
ok(not problems, "a sound event previews without problems", str(problems))

# The cap that used to drop work in silence.
many = {"id": "o", "name": "Many", "events": [{"type": "create", "actions": [
    {"kind": "set_var", "var": "v%d" % n, "value": n} for n in range(14)]}]}
_, probs = gbabuild.preview_event_c({"objects": [many]}, many,
                                    many["events"][0])
ok(any("12" in p and "v12" in p for p in probs),
   "exceeding 12 instance variables is reported, not dropped in silence",
   str(probs))

print("\n== recipes ==")
recs = gbahelp.recipe_topics()
ok(len(recs) >= 8, "recipes are in the book", str(len(recs)))
ok(all(t.section == "Recipes" for t in recs), "...in their own section")
ok(all(any(k == "insert" for k, _ in t.body) for t in recs),
   "every recipe carries an insert block")
ok(all(any(k == "code" for k, _ in t.body) for t in recs),
   "every recipe shows its code")

# A recipe is stated to compile as written. It has to actually do so, or the
# first thing the tool teaches is that its own examples are broken -- and a
# recipe fails at BUILD time, long after the button was pressed.
GCC = None
for cand in (os.path.join(ROOT, "vendor-dl"),):
    for base, dirs, files in os.walk(cand):
        if "arm-none-eabi-gcc" in files:
            GCC = os.path.join(base, "arm-none-eabi-gcc")
            break
    if GCC:
        break
if GCC:
    import subprocess
    import tempfile
    RT = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay/"
                            "opt/notebook/gbaruntime")
    tmp = tempfile.mkdtemp(prefix="gbahelp-recipes-")
    for rid, title, where, _blurb, code in gbahelp.RECIPES:
        src = os.path.join(tmp, rid + ".c")
        # A recipe is compiled the way the tool will emit it. An event recipe
        # goes inside a function; a script recipe goes at file scope. Compiling
        # both the same way is how a recipe that cannot work where it is filed
        # passes a test and fails on the author's first build.
        scope = "script" if where.lower().startswith("script") else "event"
        with open(src, "w", encoding="utf-8") as fh:
            fh.write('#include "gba.h"\n#include "runtime.h"\n')
            # The engine's own globals, which a recipe may reach for.
            fh.write("extern s32 nb_global[32];\n")
            if scope == "script":
                fh.write(code + "\n")
            else:
                fh.write("void recipe_%s(Instance *self) {\n%s\n}\n"
                         % (rid, code))
        r = subprocess.run(
            [GCC, "-mcpu=arm7tdmi", "-mthumb-interwork", "-ffreestanding",
             "-nostdlib", "-O2", "-Wall", "-I", RT, "-c", src,
             "-o", os.path.join(tmp, rid + ".o")],
            capture_output=True, text=True)
        ok(r.returncode == 0, "recipe compiles: " + title,
           (r.stderr or "").strip().split("\n")[0][:120])
    # The routing has to MATTER. If a script recipe also compiled as event
    # code then the scope field is decoration and the day it is wrong nothing
    # says so. Prove the wrong placement is rejected by the compiler.
    # GCC accepts a nested function as a GNU extension, so "does not compile
    # inside a function" is NOT true of every script recipe -- only of the ones
    # using a construct C forbids outright, such as a static local function.
    # Assert the structural reason instead, and the compiler only where it
    # genuinely applies.
    _mis = [r for r in gbahelp.RECIPES if r[2].lower().startswith("script")]
    ok(bool(_mis), "there are script-scope recipes to check")
    for _rid, _title, _w, _b, _code in _mis:
        ok(re.search(r"^\s*(?:static\s+)?(?:const\s+)?[A-Za-z_][\w ]*\**"
                     r"\s*\w+\s*\(", _code, re.M) is not None
           or re.search(r"^\s*static\b", _code, re.M) is not None,
           "%s declares at file scope, which is why it is a script" % _title)
    for rid, title, _w, _b, code in [r for r in _mis if "static void" in r[4]]:
        src = os.path.join(tmp, rid + "_wrong.c")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write('#include "gba.h"\n#include "runtime.h"\n')
            fh.write("void wrong(Instance *self) {\n%s\n}\n" % code)
        r = subprocess.run(
            [GCC, "-mcpu=arm7tdmi", "-mthumb-interwork", "-ffreestanding",
             "-nostdlib", "-O2", "-Wall", "-I", RT, "-c", src,
             "-o", os.path.join(tmp, rid + "_wrong.o")],
            capture_output=True, text=True)
        ok(r.returncode != 0,
           "%s is REJECTED as event code, which is why it is a script" % title)
else:
    print("  SKIP recipe compilation (no arm-none-eabi-gcc in vendor-dl)")

print("\n== course code blocks compile ==")
# This gate is deliberately limited to authored Course C() blocks.  Derived
# Engine-call signatures are declarations copied from runtime.h, while Recipes
# have the scope-aware gate above.  A block is skipped only when it contains an
# ellipsis token or is empty after whitespace; lone declarations still compile.
# Those skips are printed and counted: an ordinary compiler failure must never
# disappear into the incompleteness rule.
_course_code = []
for _topic in gbahelp.all_topics():
    if _topic.section == "Course":
        for _body_index, (_kind, _code) in enumerate(_topic.body):
            if _kind == "code":
                _course_code.append((_topic, _body_index, _code))

_course_skipped = []
_course_compiled = 0
if GCC:
    _course_tmp = tempfile.mkdtemp(prefix="gbahelp-course-")
    # Generated projects supply these names around event fragments.  They are
    # data/constant fixtures, never function declarations, so a misspelled or
    # invented API still fails through -Werror=implicit-function-declaration.
    _course_context = r'''
#define OBJ_ENEMY 1
#define OBJ_SPARK 2
#define FACING_RIGHT 1
#define FACING_LEFT 2
#define FACING_IDLE 0
#define ST_IDLE 0
#define ST_WALK 1
#define ANIM_WALK 1
#define ANIM_IDLE 0
static const u32 tiles[1] = { 0 };
static const u32 gfx_town_tiles[1] = { 0 };
static const u32 tile_words = 1;
static s32 angle, speed, n, phase, x, y, total, count, dx, dy;
static void idle_step(void) {}
static void walk_step(void) {}
'''
    _file_scope = re.compile(
        r"^\s*(?:(?:static\s+)?(?:const\s+)?[A-Za-z_]\w*(?:\s+|\s*\*\s*)+)"
        r"[A-Za-z_]\w*\s*\([^;]*\)\s*(?:\{|;)", re.S)
    for _topic, _body_index, _code in _course_code:
        _ident = "%s[%d]" % (_topic.tid, _body_index)
        if "..." in _code or not _code.strip():
            _course_skipped.append((_ident, "ellipsis or empty block"))
            continue
        _src = os.path.join(_course_tmp, "%s_%d.c" %
                            (_topic.tid, _body_index))
        _has_function = re.search(
            r"^\s*(?:static\s+)?[A-Za-z_]\w*(?:\s+|\s*\*\s*)+"
            r"[A-Za-z_]\w*\s*\([^;]*\)\s*\{", _code, re.M) is not None
        _is_file = _file_scope.match(_code) is not None or _has_function
        with open(_src, "w", encoding="utf-8") as _fh:
            _fh.write('#include "gba.h"\n#include "runtime.h"\n')
            _fh.write(_course_context)
            # Teaching code may name anything the editors could have made:
            # the generator emits NB_OBJ_*/NB_SPR_*/NB_SND_*/NB_ROOM_*
            # constants per project, so the harness grants whatever names the
            # block uses. Zero is a valid index everywhere it could be used.
            for _nc in sorted(set(re.findall(
                    r"\bNB_(?:OBJ|SPR|SND|ROOM)_[A-Z0-9_]+", _code))):
                _fh.write("#define %s 0\n" % _nc)
            if _is_file:
                _fh.write("\n" + _code + "\n")
            else:
                _fh.write("\nvoid course_block_%s(Instance *self) {\n%s\n}\n"
                          % (_body_index, _code))
        _result = subprocess.run(
            [GCC, "-mcpu=arm7tdmi", "-mthumb-interwork", "-ffreestanding",
             "-nostdlib", "-O2", "-Wall",
             "-Werror=implicit-function-declaration", "-I", RT, "-c", _src,
             "-o", os.path.join(_course_tmp, "%s_%d.o" %
                                 (_topic.tid, _body_index))],
            capture_output=True, text=True)
        _course_compiled += 1
        ok(_result.returncode == 0,
           "course code compiles: %s / block %d" %
           (_topic.title, _body_index),
           (_result.stderr or "").strip().split("\n")[0][:160])
else:
    print("  SKIP course compilation (no arm-none-eabi-gcc in vendor-dl)")
for _ident, _reason in _course_skipped:
    print("  SKIP course code %s -- %s" % (_ident, _reason))
print("     course C() blocks: %d total, %d compiled, %d skipped" %
      (len(_course_code), _course_compiled, len(_course_skipped)))
ok(GCC is None or _course_compiled + len(_course_skipped) == len(_course_code),
   "every course C() block is compiled or visibly skipped")

print("\n== text mandate ==")
# Same rule the OS applies everywhere: UI text describes function. Second person
# is the failure this content is most exposed to, being instructional.
BANNED = re.compile(r"\b(you|your|yours|we|our|let's|please|simply|just "
                    r"|easy|easily|don't worry|feel free)\b", re.I)
hits = []
for t in topics:
    for line in t.text().split("\n"):
        m = BANNED.search(line)
        if m:
            hits.append("%s: %s" % (t.tid, line.strip()[:70]))
ok(not hits, "no second person or reassurance in the content",
   " | ".join(hits[:5]))

# Tofu: the shipped face has no emoji. Anything outside the Latin/typographic
# range renders as a box on the guest.
ALLOWED = set("•—–‘’“”·…→"
              "≤≥×±✓○▸⚠éö")
bad = set()
for t in topics:
    for ch in t.text():
        if ord(ch) > 0x7F and ch not in ALLOWED:
            bad.add(ch)
ok(not bad, "no character outside the shipped face",
   " ".join("U+%04X %s" % (ord(c), c) for c in sorted(bad)))

print("\n== search ==")
hay = {t.tid: t.text().lower() for t in topics}
for term, expect in (("hdma", "c14"), ("fixed point", "c04"),
                     ("palette", "g_palettes"), ("sram", "g_saves"),
                     ("semicolon", "c02")):
    ok(any(term in v for v in hay.values()), "search finds: " + term)
    ok(term in hay.get(expect, ""), "%r is in %s" % (term, expect))

# Drive the real search handler without constructing GTK. A zero-result query
# replaces the article body; the next matching query must replace that stale
# empty message too, rather than only repopulating the sidebar.
class _SearchEntry:
    def __init__(self, text): self.text = text
    def get_text(self): return self.text


class _SearchPane:
    topics = [gbahelp.Topic("one", "Alpha", "Test", [gbahelp.P("first")]),
              gbahelp.Topic("two", "Beta", "Test", [gbahelp.P("second")])]
    _by_id = {t.tid: t for t in topics}
    _current = "one"

    def __init__(self):
        self.filled = []
        self.shown = []
        self.empty = []

    def _fill_list(self, rows): self.filled.append([t.tid for t in rows])
    def show_topic(self, tid): self._current = tid; self.shown.append(tid)
    def _render_empty(self, q): self.empty.append(q)


_search_probe = _SearchPane()
gbahelp.HelpPane._on_search(_search_probe, _SearchEntry("missing"))
gbahelp.HelpPane._on_search(_search_probe, _SearchEntry("beta"))
ok(_search_probe.empty == ["missing"] and _search_probe.shown == ["two"],
   "a match replaces the stale no-results article",
   "empty=%r shown=%r" % (_search_probe.empty, _search_probe.shown))
gbahelp.HelpPane._on_search(_search_probe, _SearchEntry(""))
ok(_search_probe.filled[-1] == ["one", "two"]
   and _search_probe.shown[-1] == "two",
   "clearing search restores all topics and readable content",
   "filled=%r shown=%r" % (_search_probe.filled, _search_probe.shown))


class _FakeList:
    def __init__(self): self.selected = None
    def get_selected_row(self): return self.selected
    def select_row(self, row): self.selected = row


class _FakeScroll:
    def get_vadjustment(self): return None


row = object()
sync_probe = _SearchPane()
sync_probe._topic_rows = {"two": row}
sync_probe._list = _FakeList()
sync_probe._bsc = _FakeScroll()
sync_probe._render = lambda _topic: None
gbahelp.HelpPane.show_topic(sync_probe, "two")
ok(sync_probe._current == "two" and sync_probe._list.selected is row,
   "showing a rebuilt search result selects its matching sidebar row")

print("\n%s  (%d failed)" % ("FAILURES: " + ", ".join(FAIL) if FAIL
                             else "all checks pass", len(FAIL)))
sys.exit(1 if FAIL else 0)
