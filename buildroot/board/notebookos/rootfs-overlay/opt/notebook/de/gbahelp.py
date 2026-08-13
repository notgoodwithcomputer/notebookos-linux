#!/usr/bin/env python3
"""
Help — the SDK's reference and its course in applied C.

WHY THIS EXISTS
---------------
The machine has no internet. The built-in reference is therefore not supporting
material for documentation that lives elsewhere; it is the only documentation
that exists, and it has to be complete rather than introductory.

It carries two things that are usually separate products:

  REFERENCE   every action, every engine call, every language construct, every
              hardware register the suite exposes.

  COURSE      a sequence that starts at the drag-drop sheet an author is already
              using and ends at hardware registers, interrupts and DMA — in C,
              the same C the generator emits.

They are one pane because they are one subject. Part 0 of docs/GBA-SDK-SPEC.md
makes the tool three levels of one capability — actions, script, direct C — and
binds them with the rule that each level lowers to the one below it and any
level can be shown the level under it. That rule is what makes a course
possible at all: a lesson never has to invent an example, because the author's
own project is already the example. Lesson 1 is a button that shows the C an
object the author built five minutes ago compiles to.

TWO THINGS ABOUT THE CONTENT
----------------------------
1. The reference is DERIVED, not written. Actions come from gbasdk's own
   ACTION_DEFS, engine calls are parsed out of runtime.h with the comments that
   sit above them. Hand-copying 45 action signatures produces a reference that
   is wrong within a month; deriving it means the reference cannot disagree with
   the tool. Only prose that explains — the course, the guides, the hardware
   chapters — is written here.

2. Checkpoints read the real project. A lesson that says "add an Execute Code
   action" verifies against the open project, not against a sandbox. The course
   is applied because it has nothing else to be applied to.

LANGUAGE RULE (spec II.16, enforced by tools/voice_check.py)
Every word describes function. No second person, no reassurance, no
encouragement. A heading is a noun; an instruction is an imperative; a
description states behaviour; a constraint states a limit.
"""
import os
import re

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Pango  # noqa: E402

try:
    from nbi18n import _t
except Exception:                                           # noqa: BLE001
    def _t(text):
        """Standalone (tools/gbahelp_selftest.py): no catalog, no change."""
        return text

RUNTIME_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "gbaruntime")


# ---------------------------------------------------------------------------
# Content blocks. A topic body is a list of these.
# ---------------------------------------------------------------------------
def H(text):
    """Sub-heading."""
    return ("h", text)


def P(text):
    """Paragraph."""
    return ("p", text)


def C(code):
    """Code block, shown in the monospace face with no wrapping."""
    return ("code", code.strip("\n"))


def L(items):
    """Bulleted list."""
    return ("list", list(items))


def T(head, rows):
    """Table: a header row and body rows, all strings."""
    return ("table", (list(head), [list(r) for r in rows]))


def N(text):
    """A constraint or a trap, set apart. Reserved for things that cost hours
    when they are found the hard way."""
    return ("note", text)


def DO(text):
    """A step to carry out in the app, in the author's own project."""
    return ("do", text)


def CHK(check_id, text):
    """A step the pane can verify against the open project."""
    return ("check", (check_id, text))


class Topic:
    __slots__ = ("tid", "title", "section", "body", "lesson")

    def __init__(self, tid, title, section, body, lesson=False):
        self.tid = tid
        self.title = title
        self.section = section
        self.body = body
        self.lesson = lesson

    def text(self):
        """Everything in the topic as one searchable string."""
        out = [self.title]
        for kind, v in self.body:
            if kind in ("h", "p", "code", "do", "note"):
                out.append(v)
            elif kind == "list":
                out += v
            elif kind == "check":
                out.append(v[1])
            elif kind == "insert":
                out.append(v[1])
                out.append(v[2])
            elif kind == "table":
                head, rows = v
                out += head
                for r in rows:
                    out += r
        return "\n".join(out)


# ===========================================================================
# THE COURSE — applied C, starting from the sheet
# ===========================================================================
# Ordering rule: each lesson is reachable from the one before it using only the
# project the author already has. No lesson requires starting a new project, and
# no lesson introduces a concept the generator does not itself emit — everything
# taught here can be read back out of the author's own build.

COURSE = [

    Topic("c01", "1 · What a build produces", "Course", [
        P("A project is not run. It is translated to C, compiled by GCC for the "
          "ARM7TDMI, and linked into a cartridge image. Nothing of the SDK "
          "reaches the cartridge: no interpreter, no runtime loader, no project "
          "file. The ROM contains compiled machine code and the data it uses."),
        P("Three stages, all of them inspectable:"),
        T(["Stage", "Input", "Output"],
          [["Generate", "the project", "one C file plus asset tables"],
           ["Compile", "that C file", "ARM object code"],
           ["Link and fix", "objects and runtime", "a .gba with a valid header"]]),
        P("The first stage is the one worth reading. Every row of the action "
          "sheet becomes one or a few lines of C, and the mapping is direct "
          "enough that the C can be read by whoever wrote the actions."),
        DO("Open an object with at least one event. Press Show C on the "
           "event, in the Help menu."),
        # A new project holds nothing, so lesson 1 of the course would open on
        # a checkpoint with no route to satisfying it. The example game already
        # carries objects, events and actions: say where it is rather than
        # requiring a game to be built before the first lesson can be read.
        DO("With an empty project: File \u25b8 Open Example Game."),
        CHK("has_event", "The project has an object with an event."),
        P("The pane that opens holds the exact text the generator hands to GCC "
          "for that event, unedited. Later lessons read it line by line."),
        N("Show C is a view, not an export. Editing the project changes it; "
          "editing it changes nothing. Code written by hand belongs in an "
          "Execute Code action, which lesson 11 covers."),
    ], lesson=True),

    Topic("c02", "2 · Statements", "Course", [
        P("A Move Fixed action set to right at speed 2 generates:"),
        C("self->hspeed = 2;\nself->vspeed = 0;"),
        P("Two statements. Each ends in a semicolon, which terminates a "
          "statement — it is not a separator between them and it is not "
          "optional. Whitespace and line breaks carry no meaning; the semicolons "
          "are what divide the work."),
        P("A statement runs to completion before the next one starts, top to "
          "bottom. An action sheet has the same rule, which is why the "
          "translation is one row to one statement so often."),
        H("Comments"),
        C("/* ignored by the compiler, in any amount */\n// ignored to the end of the line"),
        P("The generator emits comments to mark which object and event a block "
          "came from. They cost no ROM space."),
        H("Blocks"),
        P("Braces group statements into one statement:"),
        C("if (self->x > 200) {\n    self->hspeed = -2;\n    rt_play_sound(1);\n}"),
        P("This is the same nesting the sheet draws as indented rows under an "
          "If action."),
        DO("Read the C for an event holding several actions."),
        CHK("has_action", "The project has actions to read."),
    ], lesson=True),

    Topic("c03", "3 · Variables and types", "Course", [
        P("A variable is a named piece of memory with a fixed size chosen when "
          "it is declared. The size is not a detail on this machine: 32 KB of "
          "fast IWRAM is the entire working set for a game."),
        T(["Type", "Bits", "Range", "Use"],
          [["u8", "8", "0 … 255", "counters, indices, flags"],
           ["s8", "8", "-128 … 127", "small signed offsets"],
           ["u16", "16", "0 … 65535", "colours, tile numbers, registers"],
           ["s16", "16", "-32768 … 32767", "screen coordinates, speeds"],
           ["u32", "32", "0 … 4294967295", "register words, bit masks"],
           ["s32", "32", "±2.1 billion", "score, fixed-point maths"]]),
        C("s16 x = 120;\nu8  frame = 0;\nu32 mask = IRQ_VBLANK | IRQ_TIMER0;"),
        P("A declaration may state a starting value. A variable read before "
          "anything is stored in it holds whatever was in that memory before, "
          "which is rarely zero and never reliable."),
        H("No floating point"),
        N("The ARM7TDMI has no floating-point unit. Writing float or double "
          "compiles, then calls a software library that costs hundreds of cycles "
          "per operation — enough that a few dozen of them per frame miss the "
          "frame. Fixed point, in lesson 4, is the alternative and it is what "
          "the engine itself uses."),
        H("Where a variable lives"),
        L(["Declared inside a function: on the stack, gone when the function "
           "returns, effectively free.",
           "Declared outside any function: permanent, and counted against RAM "
           "for the whole run.",
           "An instance variable (self->var[0]): one copy per live instance, "
           "twelve slots per object."]),
        DO("Add an Execute Code action and declare a variable in it."),
        CHK("code_declares_var", "An Execute Code action declares a variable."),
    ], lesson=True),

    Topic("c04", "4 · Fixed point", "Course", [
        P("Fixed point stores a fraction in an integer by agreeing where the "
          "point sits. In 8.8, the low 8 bits are the fraction and the value 256 "
          "means 1.0. The CPU sees ordinary integers and integer instructions, "
          "which is the entire point."),
        C("#define FIX 8                 /* 8 fractional bits */\n"
          "s32 speed = 1 << FIX;         /* 1.0 pixels per frame  */\n"
          "s32 half  = 1 << (FIX - 1);   /* 0.5                   */\n"
          "s32 pos   = 0;\n"
          "\n"
          "pos += speed + half;          /* 1.5 pixels            */\n"
          "s16 screen_x = pos >> FIX;    /* back to whole pixels  */"),
        H("The three rules"),
        L(["Addition and subtraction work unchanged, provided both values use "
           "the same number of fractional bits.",
           "Multiplication doubles the fractional bits: shift the result right "
           "by FIX to restore the format.",
           "Division halves them: shift the numerator left by FIX first."]),
        C("#define FIX 8\n"
          "s32 a = 3 << FIX, b = 2 << FIX;\n"
          "s32 product  = (a * b) >> FIX;   /* 6.0 */\n"
          "s32 quotient = (a << FIX) / b;   /* 1.5 */"),
        N("A 16.16 multiply overflows s32 before the shift: 1.0 × 1.0 is "
          "0x10000 × 0x10000, which does not fit. Either use 8.8 for anything "
          "multiplied, or cast to s64 for the intermediate. The engine uses 8.8 "
          "for speeds and scales for this reason."),
        H("Trigonometry"),
        P("rt_sin8 and rt_cos8 take an angle in 0 … 255 rather than degrees or "
          "radians, and return 8.8 fixed point. A full turn in 256 steps means "
          "the angle wraps by discarding the high bits, at no cost."),
        C("s32 angle = 32;                 /* an eighth of a turn */\n"
          "s32 speed = 2 << 8;             /* 2.0 pixels per frame, in 8.8 */\n"
          "s32 vx = (rt_cos8(angle) * speed) >> 8;\n"
          "s32 vy = (rt_sin8(angle) * speed) >> 8;"),
    ], lesson=True),

    Topic("c05", "5 · Conditions", "Course", [
        P("An If Variable action comparing hp below 1 generates:"),
        C("if (self->var[0] < 1) {\n    rt_destroy(self);\n}"),
        P("The condition is any expression. Zero is false; every other value is "
          "true. There is no separate boolean type in use here."),
        T(["Operator", "Meaning"],
          [["==  !=", "equal, not equal"],
           ["<  >  <=  >=", "ordering"],
           ["&&  ||", "and, or — stop early once the answer is known"],
           ["!", "logical not"],
           ["&  |  ^  ~", "bitwise and, or, exclusive or, complement"],
           ["<<  >>", "shift left, shift right"]]),
        N("== compares, = assigns. `if (x = 0)` stores zero in x and is always "
          "false. The compiler warns; the warning is worth reading."),
        H("else and else if"),
        C("if (self->hspeed > 0) {\n"
          "    rt_set_flip(self, 0, 0);        /* face right */\n"
          "} else if (self->hspeed < 0) {\n"
          "    rt_set_flip(self, 1, 0);        /* mirrored: face left */\n"
          "} else {\n"
          "    self->image_speed = 0;          /* standing still */\n"
          "}"),
        P("The action sheet has no else. This is the first thing C offers that "
          "the sheet does not, and it is why an Execute Code action inside an "
          "otherwise drag-drop object is normal rather than a failure."),
        H("switch"),
        C("#define ST_IDLE 0\n"
          "#define ST_WALK 1\n"
          "\n"
          "switch (self->var[0]) {\n"
          "case ST_IDLE:  self->hspeed = 0;  break;\n"
          "case ST_WALK:  self->hspeed = 2;  break;\n"
          "default:       break;\n"
          "}"),
        N("A case without break falls into the next one. That is occasionally "
          "wanted and usually a bug."),
        DO("Add an Execute Code action containing an if."),
        CHK("code_has_if", "An Execute Code action contains an if."),
    ], lesson=True),

    Topic("c06", "6 · Loops and the frame budget", "Course", [
        P("Three forms, all of them the same machine:"),
        C("for (int i = 0; i < 8; i++) {\n"
          "    rt_create(NB_OBJ_SPARK, self->x, self->y);\n"
          "}\n"
          "\n"
          "int n = 12;\n"
          "while (n > 0) {\n"
          "    n = n / 2;\n"
          "}\n"
          "\n"
          "do {\n"
          "    n++;\n"
          "} while (n < 4);      /* body runs at least once */"),
        P("break leaves the loop; continue skips to its next iteration."),
        H("The budget"),
        P("A frame is 280,896 CPU cycles at 16.78 MHz, and about 83 percent of "
          "that is drawing time during which VRAM is contended. Work in a Step "
          "event runs once per instance per frame, so a loop of 100 iterations "
          "inside a Step event on 20 instances is 2,000 iterations per frame."),
        T(["Operation", "Approximate cycles"],
          [["add, subtract, shift, compare", "1"],
           ["multiply", "1 – 4, by operand size"],
           ["divide", "no instruction — see rt_div"],
           ["EWRAM read (s16)", "3 (wait states)"],
           ["IWRAM read", "1"],
           ["ROM read, sequential", "1 – 2 with prefetch"]]),
        N("Division has no instruction on this CPU. Writing / on two variables "
          "calls a software routine of roughly 20 to 100 cycles. Dividing by a "
          "constant power of two is a shift and is free; rt_div uses the BIOS "
          "and is the fastest general form."),
        DO("Add an Execute Code action containing a loop."),
        CHK("code_has_loop", "An Execute Code action contains a for or while."),
    ], lesson=True),

    Topic("c07", "7 · Functions", "Course", [
        P("A function names a piece of work so it can be written once:"),
        C("static s16 clamp(s16 v, s16 lo, s16 hi)\n"
          "{\n"
          "    if (v < lo) return lo;\n"
          "    if (v > hi) return hi;\n"
          "    return v;\n"
          "}"),
        L(["The first word is the type of the value returned. void returns "
           "nothing.",
           "Parameters are copies. Assigning to one changes nothing outside.",
           "static confines the name to this file, which lets the compiler "
           "inline it and keeps it out of the link."]),
        P("The generator writes one function per object event and calls it from "
          "the main loop. An event is already a function; lesson 1's Show C "
          "shows its opening line."),
        H("Order matters"),
        P("A function must be declared before it is used. Either define it above "
          "the code that calls it, or write a prototype first:"),
        C("static s16 clamp(s16 v, s16 lo, s16 hi);   /* prototype */"),
        H("Where a function goes"),
        P("An Execute Code action is emitted INSIDE an event function. A "
          "function written there is therefore a function inside a function, "
          "which standard C does not have; GCC accepts it as an extension, so "
          "it builds, but it is visible to nothing else and is re-declared in "
          "every event that repeats it."),
        P("A function belongs in a SCRIPT: file-scope C, emitted once before "
          "every object, callable from any Execute Code action and from any "
          "other script."),
        N("Some things are errors rather than extensions inside a function. A "
          "static variable, which is how a handler keeps state between calls, "
          "is one of them -- so an interrupt handler cannot be written in an "
          "Execute Code action at all."),
        DO("Resource \u25b8 New Script."),
        CHK("has_script", "The project has a script."),
        N("Recursion works but the stack is small. The default is a few "
          "kilobytes in IWRAM shared with everything else; a recursive descent "
          "over a large structure is the wrong shape for this machine."),
    ], lesson=True),

    Topic("c08", "8 · Arrays", "Course", [
        P("An array is a run of values of one type, indexed from zero:"),
        C("static const s16 jump_arc[16] = {\n"
          "    -6, -6, -5, -5, -4, -3, -2, -1,\n"
          "     0,  1,  2,  3,  4,  5,  5,  6\n"
          "};\n"
          "\n"
          "self->vspeed = jump_arc[self->var[0]];"),
        N("Nothing checks the index. Reading jump_arc[40] reads whatever memory "
          "follows the array and reports no error; writing there corrupts it. "
          "Index arithmetic is the author's responsibility, every time."),
        H("const goes in ROM"),
        P("An array marked const is placed in cartridge ROM and costs no RAM. An "
          "array without const is copied into RAM at startup and costs its full "
          "size for the whole run. A 4 KB table of level data marked const is "
          "free; the same table without const is an eighth of IWRAM."),
        H("Strings"),
        P("A string is an array of characters ending in a zero byte. String "
          "literals are const and live in ROM."),
        C('static const char msg[] = "READY";   /* 6 bytes: five and a zero */'),
    ], lesson=True),

    Topic("c09", "9 · The instance", "Course", [
        P("Every object placed in a room becomes an instance: a struct in a "
          "fixed array, with the fields the engine steps each frame. Inside an "
          "event, self points at the instance the event is running for."),
        T(["Field", "Type", "Meaning"],
          [["self->x, self->y", "s16", "position in room pixels"],
           ["self->hspeed, self->vspeed", "s16", "pixels per frame"],
           ["self->sprite", "u8", "sprite index currently shown"],
           ["self->image", "u8", "animation frame within the sprite"],
           ["self->alarm[0…3]", "s16", "counts down one per frame, fires at 0"],
           ["self->var[0…11]", "s32", "the object's own named variables"],
           ["self->active", "u8", "zero once destroyed"]]),
        P("A variable named in a Set Variable action becomes a slot in var[]. "
          "The mapping is by first appearance within the object, which is why "
          "the Show C for one event and for another agree on the slot numbers."),
        N("Twelve slots per instance. Past that the generator reports the "
          "dropped names rather than dropping them silently, but the code that "
          "needed them is still not built. An object needing more state is "
          "usually two objects."),
        H("Reaching other instances"),
        P("rt_nearest returns the closest live instance of an object, or 0 "
          "when none is alive — which is why the result is tested before it is "
          "read. Its type is Instance, the same struct self points at, so the "
          "other instance's variables are reachable the same way."),
        C("Instance *other = rt_nearest(self, OBJ_ENEMY);\n"
          "if (other && rt_dist_to(self, other->x, other->y) < 32) {\n"
          "    other->var[0] -= 1;\n"
          "}"),
    ], lesson=True),

    Topic("c10", "10 · Engine calls", "Course", [
        P("The runtime is an ordinary C library compiled into the ROM. Its "
          "header declares every call available to an Execute Code action, and "
          "the Engine calls section of this reference lists all of them with "
          "their units and constraints."),
        P("The calls used most:"),
        T(["Call", "Effect"],
          [["rt_create(obj, x, y)", "make an instance, return a pointer"],
           ["rt_destroy(inst)", "mark an instance dead"],
           ["rt_meeting(inst, obj)", "collision test against an object kind"],
           ["rt_key_held(KEY_A)", "held this frame"],
           ["rt_key_pressed(KEY_A)", "pressed on this frame only"],
           ["rt_play_sound(n)", "start a sound"],
           ["rt_room_goto(n)", "change room at the end of the frame"],
           ["rt_draw_text(s, x, y)", "text at a screen position"]]),
        P("Engine calls and the action sheet are the same surface. Play Sound is "
          "rt_play_sound; there is no capability behind one that the other "
          "cannot reach."),
        DO("Add an Execute Code action that calls an engine function."),
        CHK("code_calls_engine", "An Execute Code action calls an rt_ function."),
    ], lesson=True),

    Topic("c11", "11 · C inside an object", "Course", [
        P("The Execute Code action runs in order with the rows around it and "
          "sees self, every engine call and every function the project's "
          "scripts define. An object of twelve drag-drop rows with one Execute "
          "Code among them is a normal object, not a half-finished one."),
        H("Two languages, chosen on the row"),
        P("The action carries a Language setting."),
        T(["Setting", "What the text is", "Reaches"],
          [["Script", "the small subset the drag-drop actions lower to",
            "bare x, y, score; the built-in functions"],
           ["C", "handed to the compiler untouched",
            "self->x, hardware registers, script functions, all of C"]]),
        P("Everything in this course after lesson 2 is C, so set the row to C. "
          "Script is what an existing project uses and what the actions "
          "themselves compile to."),
        N("Handing C to a row set to Script is rejected as a whole: the block "
          "is replaced by a comment, the build succeeds, and that row does "
          "nothing. The problem is reported against the object and event, "
          "which is where to look when a row stops having any effect."),
        C("/* Execute Code, in a Step event */\n"
          "if (rt_key_held(KEY_LEFT) && self->x > 8) {\n"
          "    self->hspeed = -2;\n"
          "    self->image_index = ANIM_WALK;\n"
          "} else if (rt_key_held(KEY_RIGHT) && self->x < 232) {\n"
          "    self->hspeed = 2;\n"
          "    self->image_index = ANIM_WALK;\n"
          "} else {\n"
          "    self->hspeed = 0;\n"
          "    self->image_index = ANIM_IDLE;\n"
          "}"),
        H("What is available"),
        L(["self, and every field in lesson 9",
           "every engine call in runtime.h",
           "every hardware register in gba.h",
           "variables named in the same object's Set Variable actions",
           "global. names, shared across the project and saved to the cartridge"]),
        H("What a mistake produces"),
        P("A script mistake is reported against the object and event it was "
          "written in, not against a line of generated C. A build stops with "
          "the object name, the event name and the line."),
        N("Code in this action is emitted verbatim inside a function. A missing "
          "brace therefore breaks the function around it, and the error names "
          "the row rather than the brace. Balanced braces are worth checking "
          "before a long build."),
    ], lesson=True),

    Topic("c12", "12 · Hardware registers", "Course", [
        P("The GBA has no operating system. Every hardware feature is a memory "
          "address, and writing to that address is how the feature is used. "
          "gba.h names them."),
        C("REG_BG1HOFS = 32;          /* scroll background 1 right by 32px */\n"
          "REG_MOSAIC  = 0x0033;      /* 4x4 blocks on backgrounds */"),
        P("Registers are declared volatile, which stops the compiler from "
          "caching or reordering the accesses. Hardware changes them without the "
          "program's knowledge, and the program's writes have effects the "
          "compiler cannot see."),
        H("Registers the runtime writes for itself"),
        P("Some registers are rewritten every frame. The runtime finishes each "
          "VBlank by assigning REG_DISPCNT, REG_BG0HOFS, REG_BG0VOFS, "
          "REG_BG3HOFS and REG_BG3VOFS from its own state, so a value written "
          "to one of those from game code survives a single frame and is then "
          "replaced. Nothing reports this: the picture changes once and goes "
          "back."),
        P("The runtime offers a call for each of them. rt_video_mode sets the "
          "display mode; rt_view_follow, rt_view_fixed and rt_shake drive BG0 "
          "through the camera; and the room's own far layer drives BG3. Every "
          "other register in the table below is free to write directly."),
        T(["Register", "Address", "Purpose"],
          [["REG_DISPCNT", "0x4000000", "video mode, layers enabled"],
           ["REG_DISPSTAT", "0x4000004", "VBlank/HBlank status, IRQ enables"],
           ["REG_VCOUNT", "0x4000006", "scanline being drawn, 0 … 227"],
           ["REG_BGnCNT", "0x4000008 +n×2", "priority, tile base, size"],
           ["REG_BGnHOFS/VOFS", "0x4000010 +n×4", "scroll offsets, write-only"],
           ["REG_KEYINPUT", "0x4000130", "buttons, 0 when pressed"],
           ["REG_IE / REG_IF / REG_IME", "0x4000200 +", "interrupt control"]]),
        N("REG_BGnHOFS is write-only. Reading it returns nothing useful, so a "
          "scroll position must be kept in a variable and written out, never "
          "read back."),
        H("Memory map"),
        T(["Region", "Address", "Size", "Note"],
          [["IWRAM", "0x3000000", "32 KB", "32-bit bus, no wait states"],
           ["EWRAM", "0x2000000", "256 KB", "16-bit bus, 2 wait states"],
           ["Palette", "0x5000000", "1 KB", "512 colours, 16-bit writes only"],
           ["VRAM", "0x6000000", "96 KB", "tiles and maps, 16-bit writes only"],
           ["OAM", "0x7000000", "1 KB", "128 sprite entries"],
           ["ROM", "0x8000000", "≤32 MB", "the cartridge"],
           ["SRAM", "0xE000000", "32 KB", "saves, 8-bit access only"]]),
        N("VRAM, palette and OAM reject 8-bit writes. Storing one byte there "
          "writes the byte to both halves of the 16-bit word — a common source "
          "of corrupted tiles that looks like a graphics bug and is a width bug. "
          "Never write a u8 into VRAM."),
        DO("Add an Execute Code action that writes a hardware register."),
        CHK("code_writes_register", "An Execute Code action writes a REG_ name."),
    ], lesson=True),

    Topic("c13", "13 · Interrupts and timers", "Course", [
        P("An interrupt suspends the program, runs a handler, and resumes. It is "
          "how work is synchronised to the display without spending the frame "
          "waiting for it."),
        C("static volatile u32 vblank_seen;\n"
          "\n"
          "static void on_vblank(void)\n"
          "{\n"
          "    vblank_seen = 1;\n"
          "}\n"
          "\n"
          "void install_vblank_handler(void)\n"
          "{\n"
          "    rt_irq_set(IRQ_VBLANK, on_vblank);\n"
          "}"),
        P("Passing 0 as the handler removes it and disables that source. The "
          "sources are named in gba.h: IRQ_VBLANK, IRQ_HBLANK, IRQ_VCOUNT, "
          "IRQ_TIMER0 … 3, IRQ_DMA0 … 3, IRQ_SERIAL, IRQ_KEYPAD, IRQ_GAMEPAK."),
        N("A handler is a function, so it goes in a script, not in an Execute "
          "Code action -- see lesson 7. Arming it is one call, which can go "
          "anywhere."),
        N("A handler runs with interrupts disabled and delays every other "
          "source, including the VBlank the display is waiting on. VBlank is "
          "about 83 scanlines, roughly 4,600 cycles. Work longer than that "
          "belongs in the main loop with a flag set by the handler."),
        H("Timers"),
        P("Four 16-bit counters. Each counts up to overflow, so rt_timer_start "
          "takes the period and performs the subtraction:"),
        C("static volatile u32 ticks;\n"
          "\n"
          "static void on_tick(void)\n"
          "{\n"
          "    ticks++;\n"
          "}\n"
          "\n"
          "void start_tick_timer(void)\n"
          "{\n"
          "    rt_timer_start(0, 273, TM_FREQ_1024 | TM_IRQ);\n"
          "    rt_irq_set(IRQ_TIMER0, on_tick);      /* ~60 Hz */\n"
          "}"),
        T(["Flag", "Tick length"],
          [["TM_FREQ_1", "1 CPU cycle — 16.78 MHz"],
           ["TM_FREQ_64", "64 cycles — 262 kHz"],
           ["TM_FREQ_256", "256 cycles — 65.5 kHz"],
           ["TM_FREQ_1024", "1024 cycles — 16.4 kHz"]]),
        P("TM_CASCADE makes a timer count the overflows of the one below it, "
          "which is how a counter wider than 16 bits is built. Timer 0 cannot "
          "cascade — there is nothing below it."),
        N("Sample playback clocks a timer at the sample period and has DMA1 or "
          "DMA2 refill the sound FIFO on its request. A project using PCM audio "
          "therefore has fewer timers and fewer DMA channels free than the "
          "counts suggest."),
    ], lesson=True),

    Topic("c14", "14 · DMA and per-scanline effects", "Course", [
        P("DMA copies memory without the CPU. For anything of size it is faster "
          "than a loop, and it is the only practical way to move a full "
          "background into VRAM inside one VBlank."),
        C("rt_dma(3, (void *)0x6000000, tiles, tile_words, DMA_32);"),
        H("HDMA"),
        P("A DMA set to repeat at every HBlank performs one transfer per "
          "scanline, for the whole frame, with no CPU involvement. One entry of "
          "a 160-entry table is written to a register per line."),
        C("static u16 wave[160];\n"
          "\n"
          "for (int y = 0; y < 160; y++)\n"
          "    wave[y] = (rt_sin8((y * 4 + phase) & 255) * 6) >> 8;\n"
          "\n"
          "rt_hdma_start(0, (void *)&REG_BG0HOFS, wave, 1);"),
        P("The same shape produces a gradient sky by targeting a palette entry, "
          "a split screen by targeting REG_BG0VOFS, and a perspective floor by "
          "targeting the affine registers."),
        N("Arm an HDMA during VBlank. Arming it mid-frame starts the transfer "
          "part-way down the screen and the effect appears offset by however "
          "many lines had already been drawn."),
        T(["Channel", "Reserved for"],
          [["DMA0", "highest priority, internal memory only"],
           ["DMA1, DMA2", "the sound FIFOs when PCM audio is used"],
           ["DMA3", "general purpose, and the only one that reaches ROM"]]),
    ], lesson=True),

    Topic("c14b", "15 · Effects the hardware draws", "Course", [
        P("Four features that change what appears on screen without redrawing "
          "anything. All of them are register writes, and all of them cost the "
          "same whether they cover one pixel or the whole display."),
        H("Blending"),
        P("Two SETS of layers are named: what is blended, and what it is "
          "blended with. Naming only the first is the usual mistake -- nothing "
          "changes and nothing reports why."),
        C("rt_blend_alpha(BLD_A_OBJ, BLD_B_BG0, 10, 8);   /* sprites over BG0 */\n"
          "rt_blend_brightness(BLD_A_BG0 | BLD_A_OBJ, -16); /* to black */\n"
          "rt_blend_off();"),
        N("The two weights are 0..16 each and may sum past 16, which "
          "over-brightens rather than clipping. One layer can be in both sets "
          "at once; the result is that it blends with itself, which is not an "
          "error and looks like nothing happening."),
        H("Windows"),
        P("A rectangle, plus two layer masks: what is drawn inside it and what "
          "everywhere else. A spotlight, a status bar the map does not run "
          "under, a dialogue box that dims the world -- none of which is a "
          "drawing operation."),
        C("/* everything inside the circle of light; outside, dim */\n"
          "rt_window(0, x - 48, y - 48, 96, 96, WIN_ALL, WIN_BLEND);\n"
          "rt_blend_brightness(BLD_A_BG0 | BLD_A_OBJ, -10);"),
        N("A width of 0 covers the WHOLE SCREEN in hardware, because a right "
          "edge at or before the left edge is read as 240. rt_window clamps it "
          "to nothing instead, which is what asking for zero width means."),
        H("Mosaic"),
        P("Pixels enlarged into blocks: a dissolve, a pixelate-in, a damage "
          "flash. Sizes are 1 to 16, where 1 is off."),
        C("rt_mosaic(1, 1, 4, 4);      /* sprites blocky, backgrounds sharp */"),
        N("A layer is only affected if its own control register also carries "
          "the mosaic bit -- BGCNT_MOSAIC, or OBJ_MOSAIC in a sprite. Setting "
          "sizes alone changes nothing visible, which reads as the feature not "
          "working."),
        H("Rotation and scaling"),
        P("Any sprite turns and scales through one of 32 transform slots. "
          "rt_set_angle and rt_set_scale take an instance and the runtime "
          "assigns the slot; rt_obj_affine writes a slot directly, which is "
          "how many sprites share one transform between them."),
        C("rt_set_angle(self, angle);      /* 0..255 is a full turn */\n"
          "rt_set_scale(self, 384);        /* 384/256 = 1.5x */\n"
          "rt_obj_affine(0, angle, 384);   /* slot 0, shared by many */"),
        N("The registers underneath hold the INVERSE transform, which is why "
          "scaling UP divides. rt_set_scale and rt_obj_affine take the size "
          "meant and do that reversal."),
        H("Rotating a background"),
        P("Backgrounds turn only in display modes 1 and 2, and the runtime "
          "starts in mode 0. rt_video_mode changes it — writing REG_DISPCNT "
          "directly does not last, because the frame loop rewrites that "
          "register every VBlank. rt_video_mode_get reports the mode in "
          "effect."),
        C("rt_video_mode(1);               /* BG0/BG1 tiled, BG2 affine */\n"
          "rt_bg_affine(2, 120, 80, 120, 80, angle, 256);"),
        N("An affine room supplies BG2 with its own 8-bit map and project-level "
          "affine_tileset. Entering that room selects mode 1 automatically; "
          "rt_bg_affine turns and scales the ground without a direct write to "
          "the display-mode register."),
        T(["scale", "Result"],
          [["256", "life size"], ["512", "double"], ["128", "half"],
           ["0", "treated as 1, because the register maths divides by it"]]),
        DO("Add an Execute Code action, set to C, using one of these."),
        CHK("code_uses_effect", "An Execute Code action uses a Phase 7 effect."),
    ], lesson=True),

    Topic("c15", "16 · Compressed assets", "Course", [
        P("The BIOS holds decompression routines. They cost no cartridge space, "
          "run faster than compiled equivalents, and are how a project's "
          "graphics fit."),
        T(["Call", "Format", "Best on"],
          [["rt_lz77_vram", "LZ77", "tiles and maps — the general choice"],
           ["rt_lz77_wram", "LZ77", "data expanded into RAM"],
           ["rt_huff", "Huffman", "data with few distinct values"],
           ["rt_rl_vram", "run-length", "flat tilemaps, large solid areas"]]),
        C("rt_lz77_vram(gfx_town_tiles, (void *)0x6000000);"),
        N("The VRAM forms write 16 bits at a time because VRAM requires it. "
          "Calling the WRAM form with a VRAM destination corrupts the "
          "destination and reports nothing."),
        P("The asset pipeline writes the BIOS header word each format needs. "
          "Compressed data produced elsewhere without that word decompresses to "
          "nonsense of an arbitrary length."),
        H("Integer maths in the BIOS"),
        C("s32 q = rt_div(total, count);   /* no divide instruction on ARM7TDMI */\n"
          "u16 d = rt_sqrt(dx * dx + dy * dy);"),
        N("The BIOS divide hangs on a zero divisor. rt_div returns 0 instead, "
          "which is a defined answer rather than a correct one — a count that "
          "can reach zero still needs checking."),
    ], lesson=True),

    Topic("c16", "17 · Reading a build report", "Course", [
        P("A build reports three classes of thing, and they are not equally "
          "urgent."),
        T(["Class", "Meaning", "Build result"],
          [["Problem", "an action or script references what is absent, or a "
            "limit dropped work", "builds, does less than written"],
           ["Error", "the C did not compile", "stops"],
           ["Budget", "a resource is near or past its hardware limit",
            "builds, may fail on hardware"]]),
        P("Problems are the class worth reading first, because the ROM builds "
          "and runs. An action naming a deleted sprite, or a thirteenth "
          "variable on an object holding twelve, produces a cartridge that is "
          "quietly not the game that was written."),
        H("The limits that bite"),
        T(["Resource", "Limit"],
          [["Sprites on screen", "128 OAM entries"],
           ["Sprite pixels per scanline", "about 1210 in the default timing"],
           ["Palettes", "16 of 16 colours for sprites, 16 for backgrounds"],
           ["VRAM", "96 KB total, split between tiles and maps"],
           ["Instance variables", "12 per instance"],
           ["Globals saved", "32"],
           ["Live instances", "128"]]),
    ], lesson=True),
]


# ===========================================================================
# RECIPES — working C, inserted rather than transcribed
# ===========================================================================
# Reading a lesson and writing code from nothing are separated by a gap that
# most people do not cross on their own. Modifying something that already works
# is how the crossing is usually made: the first change is one number, the
# second is one line, and by the fourth the thing is no longer the recipe.
#
# Each of these is complete, compiles as written, and goes into an Execute Code
# action with one button. Nothing here is a fragment.

RECIPES = [
    ("r_platform", "Platformer movement", "Step event",
     "Left and right at a fixed speed, a jump that only starts from the "
     "ground, and gravity applied every frame. Gravity and jump strength are "
     "the two numbers worth changing first.",
     """
/* --- run --- */
if (rt_key_held(KEY_LEFT)) {
    self->hspeed = -2;
    rt_set_flip(self, 1, 0);
} else if (rt_key_held(KEY_RIGHT)) {
    self->hspeed = 2;
    rt_set_flip(self, 0, 0);
} else {
    self->hspeed = 0;
}

/* --- jump: only from the ground, only on the frame A goes down --- */
if (rt_on_ground(self) && rt_key_pressed(KEY_A)) {
    self->vspeed = -7;
    rt_play_sound(0);
}

/* --- gravity, every frame, capped so falling stays predictable --- */
self->vspeed += 1;
if (self->vspeed > 8) self->vspeed = 8;
"""),

    ("r_topdown", "Top-down movement", "Step event",
     "Eight directions from the pad, with the diagonal slowed so that moving "
     "corner-wise is not faster than moving straight — the mistake that makes "
     "a top-down game feel wrong without being visible.",
     """
s16 dx = 0, dy = 0;
if (rt_key_held(KEY_LEFT))  dx = -1;
if (rt_key_held(KEY_RIGHT)) dx =  1;
if (rt_key_held(KEY_UP))    dy = -1;
if (rt_key_held(KEY_DOWN))  dy =  1;

/* Diagonals cover 1.41 tiles for every 1 straight, so scale them back to
   about 0.7. In 8.8 that is 181/256. */
if (dx && dy) {
    self->hspeed = (dx * 2 * 181) >> 8;
    self->vspeed = (dy * 2 * 181) >> 8;
} else {
    self->hspeed = dx * 2;
    self->vspeed = dy * 2;
}
"""),

    ("r_state", "State machine", "Step event",
     "One variable holds what the object is doing; a switch runs only the "
     "code for that state. Every non-trivial object ends up shaped like this, "
     "and reaching for it early costs less than converting to it later.",
     """
#define ST_IDLE  0
#define ST_WALK  1
#define ST_HURT  2

switch (self->var[0]) {

case ST_IDLE:
    rt_anim_range(self, 0, 1);
    self->hspeed = 0;
    if (rt_key_held(KEY_RIGHT) || rt_key_held(KEY_LEFT))
        self->var[0] = ST_WALK;
    break;

case ST_WALK:
    rt_anim_range(self, 2, 5);
    self->hspeed = rt_key_held(KEY_LEFT) ? -2 : 2;
    if (!rt_key_held(KEY_LEFT) && !rt_key_held(KEY_RIGHT))
        self->var[0] = ST_IDLE;
    break;

case ST_HURT:
    rt_anim_once(self, 6, 8);
    self->hspeed = 0;
    if (rt_anim_done(self)) self->var[0] = ST_IDLE;
    break;
}
"""),

    ("r_dialogue", "Dialogue box", "Step event",
     "A panel with two lines of text, advanced by A and closed on the last "
     "page. Text is drawn on the BG1 layer in 8x8 cells, 30 across by 20 "
     "down, so a line holds about 26 characters inside a bordered panel.",
     """
static const char *page[] = {
    "THE DOOR IS LOCKED.",
    "SOMETHING RATTLES INSIDE.",
    "A KEY WOULD HELP.",
};
#define PAGES 3

if (self->var[0] == 0) {              /* opening */
    rt_draw_panel(1, 13, 28, 5, 1, 2);
    self->var[0] = 1;
}

rt_draw_text(3, 15, page[self->var[1]]);

if (rt_key_pressed(KEY_A)) {
    self->var[1]++;
    if (self->var[1] >= PAGES) {
        rt_clear_box(1, 13, 28, 5);
        rt_destroy(self);
    } else {
        rt_clear_box(2, 14, 26, 3);
    }
}
"""),

    ("r_hud", "Health bar and score", "Step event",
     "A HUD drawn from game state each frame. Padded digits keep the score "
     "from leaving old numerals behind when it gets shorter, which is what "
     "rt_draw_int alone would do.",
     """
/* score, six digits, top left */
rt_draw_text(1, 1, "SCORE");
rt_draw_int_pad(7, 1, nb_score, 6, 0);

/* health as a row of cells, drawn then cleared back to the current value */
rt_clear_box(21, 1, 8, 1);
{
    int hearts = nb_health / 10;
    int i;
    if (hearts > 8) hearts = 8;
    for (i = 0; i < hearts; i++)
        rt_draw_text_c(21 + i, 1, "*", 3);
}
"""),

    ("r_hdma", "Wave effect", "Create event",
     "A per-scanline scroll table walked by HDMA. The CPU builds the table "
     "once; the hardware applies one entry per line for the whole frame at no "
     "further cost. Change the 6 to change the amplitude.",
     """
static u16 wave[160];
int y;

for (y = 0; y < 160; y++)
    wave[y] = (u16)((rt_sin8((y * 4) & 255) * 6) >> 8);

rt_hdma_start(0, (void *)&REG_BG0HOFS, wave, 1);
"""),

    ("r_timer", "Timed interrupt", "Script",
     "A timer overflowing about sixty times a second, with a handler on it. "
     "The handler is a function, so it goes in a script rather than an Execute "
     "Code action -- a block of action code is emitted inside an event "
     "function, and C has no function inside a function. Call arm_tick() from "
     "a Create event.",
     """
static volatile s32 ticks;

static void on_tick(void)
{
    /* Short. A handler runs with interrupts off and delays every other
       source, including the VBlank the display is waiting on. */
    ticks++;
}

void arm_tick(void)
{
    rt_timer_start(0, 273, TM_FREQ_1024 | TM_IRQ);
    rt_irq_set(IRQ_TIMER0, on_tick);
}

s32 tick_count(void)
{
    return ticks;
}
"""),

    ("r_lookup", "Lookup table", "Script",
     "A table marked const lives in cartridge ROM and costs no RAM. The same "
     "table without const is copied into RAM at startup and costs its full "
     "size for the whole run -- which on 32 KB of IWRAM is the difference "
     "between free and expensive.",
     """
/* One full turn of a sine, 8.8 fixed point, 64 steps. In ROM: free. */
static const s16 sine64[64] = {
      0,  25,  50,  74,  98, 121, 142, 162,
    181, 198, 213, 226, 237, 245, 251, 255,
    256, 255, 251, 245, 237, 226, 213, 198,
    181, 162, 142, 121,  98,  74,  50,  25,
      0, -25, -50, -74, -98,-121,-142,-162,
   -181,-198,-213,-226,-237,-245,-251,-255,
   -256,-255,-251,-245,-237,-226,-213,-198,
   -181,-162,-142,-121, -98, -74, -50, -25
};

s16 bob(s32 frame, s16 amplitude)
{
    return (s16)((sine64[frame & 63] * amplitude) >> 8);
}
"""),

    ("r_waterfall", "Cycling waterfall", "Step event",
     "Four background palette entries rotate every six frames, animating a "
     "waterfall or torch without changing its tiles. The returned slot stops "
     "that one cycle when B is pressed.",
     """
if (self->var[0] == 0) {
    self->var[1] = rt_pal_cycle(0, 1, 4, 6);
    self->var[0] = 1;
}

if (rt_key_pressed(KEY_B)) {
    rt_pal_cycle_stop(self->var[1]);
    self->var[0] = 2;
}
"""),

    ("r_spotlight", "Sprite-shaped spotlight", "Create event",
     "The object's sprite becomes an OBJ-window stencil: a sprite-shaped hole "
     "shows BG0 through darkness. The stencil sprite itself is not drawn. The "
     "empty regular window makes everything outside all windows dark. "
     "rt_window_obj_off() removes the OBJ-window when the effect ends.",
     """
rt_set_objwin(self, 1);
rt_window_obj(WIN_BG0);
rt_window(0, 0, 0, 0, 0, 0, 0);
"""),

    ("r_mercy", "Contact damage and mercy frames", "Collision event",
     "Place this event on the player against the damaging object. Set the "
     "player object's hurt_frames field to the wanted invincibility length, "
     "then place respawn or game-over logic in its no_health event.",
     """
nb_health -= 10;
"""),

    ("r_rotate", "Rotating sprite", "Step event",
     "A sprite that turns on the spot and pulses in size. Angle is 0..255 for "
     "a full turn, and scale is 8.8 where 256 is life size — so 512 is double "
     "and 128 is half. The hardware has 32 transform slots and the runtime "
     "hands one to each turning sprite, so the cost is nothing until more "
     "than 32 are on screen at once.",
     """
/* --- a slow turn, and a size that breathes between half and life size --- */
self->var[0] = (self->var[0] + 2) & 255;      /* the angle */
self->var[1] = (self->var[1] + 3) & 255;      /* a separate phase for size */

rt_set_angle(self, self->var[0]);
rt_set_scale(self, 192 + (rt_sin8(self->var[1]) >> 2));
"""),

    ("r_bitmap", "Double-buffered bitmap", "Step event",
     "A mode-4 frame is built on the hidden page, held for VBlank, then "
     "presented. The next step draws into the page that has become hidden. "
     "Palette indices, not BGR555 colours, are stored in this mode.",
     """
static const u8 mark[16] = {
    1, 1, 0, 0,
    1, 2, 2, 0,
    0, 2, 2, 1,
    0, 0, 1, 1
};

if (self->var[0] == 0) {
    rt_bitmap_mode(4);
    self->var[0] = 1;
}

rt_bitmap_clear(0);
rt_bitmap_rect(16, 16, 80, 32, 3);
rt_bitmap_pixel(20 + rt_bitmap_page(), 20, 4);
rt_bitmap_blit(112, 72, 4, 4, mark);
rt_wait_vblank();
rt_bitmap_flip();
"""),

    ("r_affine_ground", "Turning affine ground", "Step event",
     "A room carrying an affine map enters mode 1 when it loads. This keeps "
     "the centre texture pixel under the centre screen pixel while the ground "
     "turns; scale 256 keeps it at life size.",
     """
self->var[0] = (self->var[0] + 1) & 255;
rt_bg_affine(2, 128, 128, 120, 80, self->var[0], 256);
"""),

    ("r_dissolve", "Mosaic dissolve", "Step event",
     "A transition that coarsens the picture over about half a second, then "
     "changes room. The block size is the only thing animated.",
     """
self->var[0]++;

{
    int step = self->var[0] / 2;        /* 2 frames per size */
    if (step > 15) step = 15;
    rt_mosaic(step + 1, step + 1, step + 1, step + 1);
    REG_BG0CNT |= BGCNT_MOSAIC;         /* or the sizes do nothing */
}

if (self->var[0] >= 32) {
    rt_mosaic(1, 1, 1, 1);
    rt_room_goto(1);
}
"""),

    ("r_fade", "Fade to black", "Step event",
     "A frame counter driving the hardware blend registers. Nothing is "
     "redrawn: the blend happens as the display reads each pixel.",
     """
if (self->var[0] < 16) {
    self->var[0]++;
    REG_BLDCNT = 0x00FF | (3 << 6);          /* every layer, fade to black */
    REG_BLDY   = (u16)self->var[0];          /* 0 = none, 16 = black */
} else {
    rt_room_goto(1);
}
"""),
]


def recipe_topics():
    """One topic per recipe. The Insert button is added by the pane, which is
    the only part that knows what is selected to insert into.

    A recipe declares WHERE it goes, and the two places are not
    interchangeable: an Execute Code action is emitted inside an event
    function, so a recipe that declares a function or a file-scope table only
    compiles as a script. Routing that by hand is exactly the mistake a
    beginner cannot diagnose -- the error names a brace, not the decision."""
    out = []
    for rid, title, where, blurb, code in RECIPES:
        scope = "script" if where.lower().startswith("script") else "event"
        out.append(Topic(rid, title, "Recipes", [
            P(blurb),
            P("Goes in: %s." % where),
            C(code),
            ("insert", (rid, code.strip("\n"), scope)),
        ]))
    return out


# ===========================================================================
# GUIDES — subsystem chapters that are not a sequence
# ===========================================================================
GUIDES = [

    Topic("g_play", "Playing what was built", "Guides", [
        P("File \u25b8 Build & Play builds the game and opens it in the GBA "
          "Emulator. Closing the emulator comes back here, with the project as "
          "it was."),
        N("Notebook OS runs one app at a time, so the emulator cannot sit "
          "inside this window. What Build & Play removes is the walk: "
          "exporting, closing, finding the file and opening it by hand is six "
          "steps between a change and seeing it."),
        N("The project is saved before the emulator opens. This window hides "
          "while the emulator has the screen, and anything unsaved at that "
          "moment would be one crash away from gone."),
        N("A game that does not compile stays here and says so rather than "
          "hiding behind an emulator that never opened, which reads as the "
          "machine freezing. Build \u25b8 Build Details has the reason."),
        P("A game that compiles but has problems still plays, with a count of "
          "the rows that will not do anything."),
    ]),

    Topic("g_profiler", "Where the frame goes", "Guides", [
        P("rt_prof(1) starts the profiler. The engine then measures its own "
          "step, movement and drawing, and slots 3 to 7 are the project's."),
        C("rt_prof_begin(3);\n"
          "my_work();\n"
          "rt_prof_end(3);\n"
          "int pc = rt_prof_percent(3);   /* percent of one frame */"),
        P("rt_prof_overlay() draws a corner read-out of the three engine "
          "phases and their total."),
        T(["Fact", "Figure"],
          [["One frame", "280,896 cycles at 16.78 MHz"],
           ["Counted in", "ticks of 64 cycles, so 4,389 to a frame"],
           ["Uses", "timer 2"]]),
        N("Timer 2 because the others are spoken for: timer 0 is the "
          "project's and timer 1 clocks sampled audio. Taking either would "
          "make measuring break the thing being measured."),
        N("Figures are for the last WHOLE frame. Read mid-frame they would "
          "change depending on when they were asked for."),
        H("Code in fast memory"),
        P("IWRAM is a 32-bit bus with no wait states; cartridge ROM is 16-bit "
          "with them. The collision test runs from IWRAM because it is called "
          "thousands of times a frame."),
        N("There are 32 KB of IWRAM shared with every variable in the game, so "
          "this is for the few functions that earn it rather than for the "
          "runtime at large."),
    ]),

    Topic("g_budget", "What a game costs", "Guides", [
        P("Build \u25b8 What This Game Costs, before building rather than "
          "after. A game that will not fit is otherwise found out at link "
          "time, by an error naming a section of the file rather than an "
          "asset."),
        T(["Counted", "Limit"],
          [["Sprite tiles", "1024 — every frame of every sprite at once"],
           ["Background tiles", "512"],
           ["Sprite colour sets", "16"],
           ["Objects in a room", "128 live at a time"],
           ["Sampled audio", "16 KB per second of cartridge"]]),
        P("Each line names the largest contributors underneath it. That is the "
          "point of the report: \u201cover by 40 tiles\u201d is a fact "
          "nobody can act on, and \u201cBoss is 64x64 with 20 frames, which "
          "is 1280 of them\u201d is a decision."),
        N("A 16x16 tile counts as four and a 32x32 as sixteen, because the "
          "hardware works in 8x8. Counting authored tiles would under-report "
          "by four or sixteen and let a project sail past the limit."),
        N("Objects created while the game runs are not counted, because they "
          "cannot be. The room figure is what is placed; the 128 is what can "
          "be alive at once."),
    ]),

    Topic("g_palettes_pane", "The Palettes pane", "Guides", [
        P("What the build will do with the project's colours, before building "
          "it. It runs the same allocator the generator runs, so it cannot "
          "describe an allocation different from the one that ships."),
        T(["Shown", "Meaning"],
          [["N of 16 colour sets", "hardware banks in use"],
           ["N of 240 colours", "of the 15 usable in each of 16 banks"],
           ["a row of 16 swatches", "one bank; the first is transparent, "
            "crossed rather than coloured"],
           ["N free", "colours that still fit in that bank"],
           ["the names beside a bank", "the sprites sharing it"]]),
        H("Pinning"),
        P("A sprite set to Any set is placed by the allocator, which packs in "
          "sprite order and has no way to know that two sprites are the same "
          "character. Pinning both to one set makes them share a bank, which "
          "is what lets them share tiles and cost less VRAM."),
        N("A pin that cannot fit is reported and the sprite is placed "
          "elsewhere. It is not silently ignored, and it is not honoured at "
          "the cost of dropping colours."),
        H("What the warnings mean"),
        T(["Warning", "Effect on the cartridge"],
          [["painted in more than 15 colours",
            "the colours past the fifteenth come out as holes"],
           ["run out of colour sets",
            "the sprite is drawn in another sprite's colours"],
           ["pinned to a set with no room",
            "placed in a different set than the one asked for"]]),
    ]),

    Topic("g_palettes", "Palettes", "Guides", [
        P("The constraint every project eventually meets. Colour is 15-bit: "
          "five bits each of red, green and blue, 32,768 possible colours, of "
          "which 512 can be loaded at once."),
        T(["Bank", "Count", "Entries each", "Used by"],
          [["Background", "16", "16", "BG tiles in 4bpp"],
           ["Sprite", "16", "16", "OBJ tiles in 4bpp"]]),
        P("A 4bpp tile stores one palette index per pixel in four bits, so a "
          "tile can show 15 colours plus transparency, and every tile using the "
          "same bank shares those 15."),
        N("Index 0 of every bank is transparent, not black. A palette holding 16 "
          "usable colours holds 15; the sixteenth is the hole the background "
          "shows through."),
        P("8bpp tiles use one 256-colour bank instead, at double the VRAM per "
          "tile. Worth it for a title screen, rarely worth it for a tileset."),
        H("Assignment"),
        L(["Sprites sharing a palette can share tiles and cost less VRAM.",
           "A sprite needing colours no bank has forces a new bank, and there "
           "are 16.",
           "Backgrounds assign a palette per tile in the map entry, so one "
           "tileset can span several banks."]),
    ]),

    Topic("g_sprites", "Sprites and OAM", "Guides", [
        P("128 OAM entries, each describing one sprite: position, tile number, "
          "size, palette bank, priority and flip. The hardware draws them every "
          "scanline; nothing is drawn by the program."),
        T(["Limit", "Value"],
          [["OAM entries", "128"],
           ["Sizes", "8×8 up to 64×64, square, wide and tall"],
           ["Affine sprites", "32 transformation groups"],
           ["Per-scanline budget", "about 1210 cycles of sprite fetch"]]),
        N("The per-scanline budget, not the 128 entries, is what runs out first. "
          "A row of large sprites across one line drops the sprites at the end "
          "of that line and leaves the rest of the screen correct — which looks "
          "like a flickering sprite rather than a timing limit."),
        P("OAM cannot be written while the display is drawing. Sprite updates "
          "belong in VBlank, which is what the engine's own flush does."),
    ]),

    Topic("g_dialogue", "Dialogue", "Guides", [
        P("The Say action shows a message in a panel, a character at a time, "
          "advanced by A. The engine owns the timer, the cursor, the page "
          "break and the wait, so a speaking object spends none of its twelve "
          "variables on them."),
        H("Control codes"),
        P("Written in the message itself, because dialogue is authored as "
          "text and anything assembled from parts stops being editable by "
          "whoever is writing the words."),
        T(["Code", "Effect"],
          [["\\n", "a new line"],
           ["{p}", "hold until A, then clear and carry on"],
           ["{s:N}", "frames per character; 0 puts the rest up at once"],
           ["{c:N}", "colour"],
           ["{v:N}", "the value of global N, in decimal"],
           ["{w:N}", "pause N frames without waiting for a button"]]),
        C("A shining coin!{p}{s:0}Score is now {v:0}.\n{c:3}Well done."),
        N("An unknown code is printed AS WRITTEN. Swallowing it would erase "
          "the rest of a sentence over a typo, which is the worst thing a text "
          "engine can do to somebody writing prose."),
        P("rt_say_voice(n) plays sound n on every character that is not a "
          "space; -1 turns it off."),
        H("Proportional text"),
        C("rt_vwf(1);   /* once, in a Create event */"),
        P("Each glyph in the dialogue panel then advances by its own width "
          "rather than by a whole 8-pixel cell, which fits about half again as "
          "much text in the same box. Wrapping measures in pixels to match."),
        N("The panel only. Proportional text needs a copy in memory of the "
          "tiles it draws into, because a glyph lands across a tile boundary "
          "and tiles are the only thing the hardware takes. The panel costs "
          "3.3 KB; the whole text layer would cost 19 KB to make a score "
          "read-out slightly narrower. The HUD stays on cells."),
        N("Colour resolves per tile, so a colour change takes effect at the "
          "next tile boundary rather than exactly at the letter. The "
          "alternative would recolour the two or three pixels of the previous "
          "letter that share the tile, which looks like a fault."),

        H("Measuring text"),
        C("int px    = rt_text_width(s);   /* pixels, from each glyph */\n"
          "int cells = rt_text_cells(s);   /* whole 8-pixel cells */"),
        P("Both ignore control codes, so a coloured string measures as what it "
          "will look like rather than as what was typed. rt_draw_text_centre "
          "uses the cell count, which is why a banner carrying a colour code "
          "is centred and not pushed left by the width of the code."),
        N("Text is drawn one glyph per 8-pixel cell. The per-glyph widths are "
          "there for measuring and for a proportional renderer; they are not "
          "yet what drawing advances by."),

        H("Line breaks"),
        P("The panel is 26 cells wide and 4 lines deep. A word that will not "
          "fit moves to the next line whole."),
        N("A word is measured WITHOUT its control codes, so {c:3}Bulbasaur "
          "takes ten cells and not fifteen. Measuring the raw characters wraps "
          "a line that would have fitted, and the ragged edge reads as a fault "
          "in the writing rather than in the measurement."),
        N("A word longer than the whole panel is drawn anyway rather than "
          "wrapped to nowhere."),
        DO("Add a Say action to an event."),
        CHK("has_dialogue", "An object says something."),
    ]),

    Topic("g_cutscenes", "Cutscenes", "Guides", [
        P("A scripted scene is a sequence of things happening over time. Two "
          "actions carry the parts that a Step event cannot express without a "
          "counter and a pile of branches."),
        T(["Action", "Does"],
          [["Glide To", "move to a point over a number of frames"],
           ["Lock Input", "stop or restart the player's control"]]),
        P("Sequencing uses the alarms an object already has: set an alarm, and "
          "the Alarm event is the next beat of the scene."),
        C("Create:   Lock Input on / Glide To 120,80 over 60 / Set Alarm 0 = 60\n"
          "Alarm 0:  Say \"The gate opens.\" / Set Alarm 1 = 120\n"
          "Alarm 1:  Glide To 220,80 over 45 / Lock Input off"),
        N("A glide divides the REMAINING distance by the REMAINING frames, so "
          "the last frame lands exactly on the target. Stepping by a fixed "
          "amount leaves 100 pixels over 7 frames two pixels short, and the "
          "scene looks subtly wrong with nothing to point at."),
        N("A glide overrides speed while it runs. Two things moving one "
          "instance is a fight whose cause cannot be seen."),
        N("Lock Input stops the key actions answering, so an object still "
          "running its own Step event carries on. A cutscene that leaves the "
          "player able to walk out of it is a cutscene about an empty room."),
        DO("Add a Glide To action."),
        CHK("uses_glide", "Something glides."),
    ]),

    Topic("g_menus", "Menus", "Guides", [
        P("A list with a cursor, drawn in a panel. Up and down move, A chooses, "
          "B cancels."),
        C("static const char *items[] = { \"Fight\", \"Bag\", \"Run\" };\n"
          "rt_menu_open(items, 3, 3, 3, 12);"),
        C("int r = rt_menu_step();\n"
          "if (r >= 0)      { /* chose item r */ }\n"
          "else if (r == -2) { /* cancelled */ }"),
        N("Nothing blocks. A menu that spins its own loop stops the music, the "
          "animation and the link cable while it is open, which is why "
          "rt_menu_step is called once a frame and reports what happened."),
        P("Eight rows are shown at a time. A longer list scrolls one row at a "
          "time to keep the cursor in view, with an arrow at the edge when "
          "there is more above or below."),
        N("Scrolling a page at a time would move the item the player was "
          "looking at, which is why it moves by one."),
        N("The item array must outlive the menu: the menu holds the pointer "
          "rather than copying the strings. An array declared inside the "
          "function that opened the menu is gone by the time it draws."),
        H("From the action sheet"),
        P("Show Menu takes up to four lines and the name of a variable. A menu "
          "spans frames and an action does not, so the action opens the menu "
          "and the answer arrives in that variable when it closes:"),
        T(["Value", "Meaning"],
          [["-1", "still choosing"], ["0 to 3", "the line that was chosen"],
           ["-2", "the player backed out"]]),
        P("A Step event then branches on it with an ordinary If Variable."),
        N("It is held at -1 while the menu is up, so a Step event can tell "
          "\u201cstill choosing\u201d from \u201cchose the first line\u201d. "
          "Without that the sheet reads a stale 0 and acts on a choice nobody "
          "made."),
        N("A Show Menu with no lines, or with no variable to answer into, is "
          "reported at build time. Either one produces a menu that opens and "
          "throws the choice away, which looks like the menu not working."),

        H("A menu of a table"),
        C("for (i = 0; i < nb_items_count; i++)\n"
          "    names[i] = nb_items[i].Name;\n"
          "rt_menu_open(names, nb_items_count, 3, 3, 18);"),
        P("Tables and menus are for each other: the table holds the data, the "
          "menu shows a column of it, and the index it returns indexes the "
          "table straight back."),
        DO("Add a script that opens a menu."),
        CHK("uses_menu", "A script or action opens a menu."),
    ]),

    Topic("g_tables", "Data tables", "Guides", [
        P("Rows with named columns, emitted as a C struct array. What a game of "
          "any size is mostly made of: species, moves, items, prices, dialogue "
          "keys. The alternative is a script of a thousand lines that has to "
          "be read in full before one number can be changed."),
        T(["Column type", "In C"],
          [["Number", "s32"], ["Text", "const char*"], ["Yes/No", "u8"]]),
        P("A table called Species becomes:"),
        C("typedef struct {\n"
          "    const char* Name;\n"
          "    s32 Base_HP;\n"
          "} nb_row_species;\n"
          "const nb_row_species nb_species[];\n"
          "const int nb_species_count;"),
        C("for (i = 0; i < nb_species_count; i++)\n"
          "    if (nb_species[i].Base_HP > best) best = nb_species[i].Base_HP;"),
        N("The count is emitted beside the array because C cannot ask an array "
          "its length once it has decayed to a pointer. A game that writes the "
          "row count in by hand reads past the end the first time a row is "
          "added."),
        N("Headings become C identifiers: \u201cBase HP\u201d is Base_HP. A "
          "heading may be written the way it reads; the generator rewrites it "
          "rather than refusing it."),
        N("A Number column keeps a number even when the typed text is not one. "
          "Storing the text would build C that does not compile, and the error "
          "would name the generated file rather than the cell."),
        DO("Resource \u25b8 New Table."),
        CHK("has_table", "The project has a table."),
    ]),

    Topic("g_solid", "Solid tiles and the parallax layer", "Guides", [
        P("A room's tile layer is decoration until tiles are marked solid. "
          "Solidity is a property of the TILE, set in the tile set editor, and "
          "every room using that set inherits it."),
        DO("Open a tile set, select a tile, tick Solid."),
        CHK("has_solid_tile", "A tile is marked solid."),
        N("Marking tiles solid is half of it. An OBJECT only consults the "
          "tile layer if its Stopped by setting is not Nothing -- the runtime "
          "checks that first and moves the instance without looking at the "
          "tiles at all. A floor of solid tiles holds up nothing until the "
          "thing standing on it is set to be stopped by them."),
        DO("Object editor \u25b8 Stopped by \u25b8 Solid tiles."),
        CHK("has_tilecol", "An object is stopped by solid tiles."),
        P("A solid tile is outlined in the tile strip. What it affects:"),
        T(["Call", "Behaviour with solid tiles"],
          [["rt_on_ground(self)", "true when a solid tile is directly beneath"],
           ["rt_place_free(self, x, y)", "false where a solid tile overlaps"],
           ["rt_blocked_v(self)", "true against a solid tile above or below"],
           ["movement", "an instance stops at a solid tile instead of "
            "passing through"]]),
        N("A tile is authored at 8, 16 or 32 pixels, and the hardware works in "
          "8x8 cells. Marking a 16x16 tile solid marks all four of its cells; "
          "marking only one would leave three quarters of every wall "
          "passable, which reads as collision being unreliable rather than "
          "wrong."),
        H("The room edge"),
        P("A room's outside edge is solid unless the room is set open, so a "
          "game with tile collision cannot walk out of its own level."),
        H("Auto-tiling"),
        P("Sixteen variants of one terrain, picked by which of the four "
          "orthogonal neighbours are the same terrain. Marking a tile "
          "Auto-tile declares it and the fifteen after it as one run."),
        T(["Bit", "Value", "Neighbour"],
          [["0", "1", "north"], ["1", "2", "east"],
           ["2", "4", "south"], ["3", "8", "west"]]),
        P("Variant 0 is an isolated block; variant 15 is fully enclosed. Paint "
          "the sixteen tiles in that order and painting the terrain picks the "
          "right one, and re-fits the four cells around it."),
        DO("Tile set \u25b8 select the first of sixteen \u25b8 Auto-tile."),
        CHK("has_autotile", "A tile set declares an auto-tile run."),
        N("Outside the room counts as the SAME terrain, so a field running off "
          "the edge is drawn as continuing. The alternative puts a coastline "
          "around every level."),
        N("This is authoring only. What lands in the room is an ordinary tile "
          "index, so the cartridge pays nothing and the runtime never learns "
          "it happened."),

        H("The world map"),
        P("View \u25b8 World Map draws every room and the doors between them. "
          "Clicking a room opens it, because the reason to find a room on a "
          "map is to go and change it."),
        P("A door leading to a room that has been deleted is drawn as a red "
          "stub with a cross, and the heading counts them. That is what the "
          "view is for: a room with no way back is invisible in a list and "
          "obvious on a map."),
        N("The rooms are laid out in a grid, in project order. A layout that "
          "rearranged itself when a room was added would be one nobody can "
          "navigate twice."),

        H("Doorways"),
        P("A room may carry warps: a rectangle in this room, the room it opens "
          "into, and where arriving there puts the traveller. They are checked "
          "against the instance the camera follows -- the player, by "
          "convention -- because a warp any instance could trip would fire on "
          "every wandering enemy."),
        T(["Field", "Meaning"],
          [["x, y, w, h", "the rectangle, in room pixels"],
           ["room", "the room it opens into"],
           ["tx, ty", "where the traveller lands there"]]),
        N("A warp fires on OVERLAP, not on being wholly inside it. A door one "
          "tile wide would otherwise be stepped straight over by anything "
          "moving faster than its width, which reads as a door that works only "
          "sometimes."),
        N("A warp naming a room that has been deleted is reported at build "
          "time and left out of the cartridge. Keeping it would build a door "
          "that goes nowhere, which looks exactly like a door put in the wrong "
          "place."),
        P("rt_room_goto_at(room, x, y) does the same thing from code."),
        H("Placing one"),
        P("The room editor has three modes: Objects, Tiles and Doors. In Doors "
          "mode, Leads to chooses the destination and a click puts a door in "
          "that cell; a right-click takes it away. Doors are drawn in every "
          "mode, and one with no destination is drawn crossed through."),
        DO("Room editor \u25b8 Doors \u25b8 click."),
        CHK("has_warp", "A room has a door."),
        N("A room is not offered as its own destination. A door back into the "
          "room it sits in fires the instant the traveller lands on it, so the "
          "room reloads forever."),

        H("The parallax layer"),
        P("A second tile layer, 32x32 cells, repeating, drawn behind "
          "everything and scrolled at a fraction of the camera. It shares the "
          "room's tile set, so it costs no extra tiles."),
        T(["Setting", "Effect"],
          [["far", "a 1024-entry map, or none"],
           ["far_div", "1 to 8; the camera divided by this. 2 is half speed"]]),
        N("Its size is fixed because the hardware wraps a 32x32 map. A larger "
          "one would not tile."),
    ]),

    Topic("g_backgrounds", "Backgrounds and modes", "Guides", [
        T(["Mode", "Layers", "Notes"],
          [["0", "4 tiled", "no rotation; the usual choice"],
           ["1", "2 tiled + 1 affine", "one layer rotates and scales"],
           ["2", "2 affine", "both rotate; no plain layers"],
           ["3", "1 bitmap 240×160×16bpp", "no page flip, slow to clear"],
           ["4", "1 bitmap 240×160×8bpp", "two pages, palette-indexed"],
           ["5", "1 bitmap 160×128×16bpp", "two pages, smaller"]]),
        P("Tiled modes are what a game uses. A bitmap mode has no tiles, no "
          "reuse and no free scrolling, and filling one costs most of a frame."),
        P("Each tiled layer has its own scroll offsets, size, priority and tile "
          "base. Priority orders layers against each other and against sprites, "
          "which is how a sprite is drawn behind a foreground layer."),
    ]),

    Topic("g_bitmap", "Bitmap modes", "Guides", [
        H("Three framebuffers"),
        P("rt_bitmap_mode selects mode 3, 4 or 5 and enables the BG2 "
          "framebuffer. rt_bitmap_pixel plots one clipped pixel; "
          "rt_bitmap_rect fills a clipped rectangle; rt_bitmap_clear fills "
          "the drawing page; rt_bitmap_blit copies a clipped image in the "
          "current mode's pixel format."),
        T(["Mode", "Pixels", "Pages", "Colour"],
          [["3", "240x160", "one", "u16 BGR555"],
           ["4", "240x160", "two", "8-bit BG palette index"],
           ["5", "160x128", "two", "u16 BGR555"]]),
        N("Mode 5 is smaller. A 240-pixel stride in mode 5 crosses the "
          "160-pixel row boundary and draws a diagonal instead of a row."),
        H("Hidden-page drawing"),
        C("rt_bitmap_clear(0);\n"
          "rt_bitmap_rect(24, 24, 48, 16, RGB15(31, 0, 0));\n"
          "rt_bitmap_pixel(120, 80, RGB15(31, 31, 31));\n"
          "rt_wait_vblank();\n"
          "rt_bitmap_flip();"),
        P("Modes 4 and 5 always draw to the hidden page. rt_bitmap_page "
          "reports that drawing page as 0 or 1. The runtime's rt_vsync frame "
          "boundary paired with rt_bitmap_flip presents the completed page "
          "during VBlank, then makes the other page the next drawing target. "
          "Author code uses rt_wait_vblank before the flip because rt_vsync "
          "belongs to the runtime loop. Mode 3 has no hidden page, so every "
          "edit is visible and rt_bitmap_flip does nothing."),
        N("A mode-4 pixel occupies one byte, but VRAM ignores byte writes. "
          "rt_bitmap_pixel performs a read-modify-write of the containing "
          "halfword; a direct byte plot silently loses every second pixel."),
        H("Layers and sprite memory"),
        N("BG0, BG1 and BG3 do not exist in a bitmap mode. The runtime's text "
          "and dialogue layers therefore are not drawn there."),
        N("OBJ tiles begin at 0x06014000 in bitmap modes. Sprite tile numbers "
          "below 512 land in the framebuffer instead of safe OBJ tile memory."),
        DO("Insert the Double-buffered bitmap recipe in a Step event."),
        CHK("code_calls_engine", "An Execute Code action calls an rt_ function."),
    ]),

    Topic("g_affine_ground", "Affine ground", "Guides", [
        H("Room data"),
        P("A project-level affine_tileset supplies 8-bit tiles. A room may "
          "carry a 16x16 or 32x32 affine map whose cells are 8-bit tile "
          "indices. Loading that room selects display mode 1 automatically "
          "and places the affine ground on BG2."),
        T(["Map size", "Ground area"],
          [["16x16 cells", "128x128 pixels"],
           ["32x32 cells", "256x256 pixels"]]),
        H("The layer trade"),
        P("Mode 0 has four text backgrounds; mode 1 has two text backgrounds "
          "and one affine background. An affine room gives up its flat tile "
          "layer because the affine layer IS the ground, and gives up its "
          "parallax layer as well."),
        N("BG0 and BG1 swap duties in an affine room: BG0 carries text and "
          "BG1 carries the dialogue panel. A lower-numbered background wins a "
          "priority tie, so the text remains above its box."),
        H("Turning and scaling"),
        C("rt_bg_affine(2, 128, 128, 120, 80, angle, 256);"),
        P("rt_bg_affine puts texture pixel (128,128) at screen pixel "
          "(120,80), then turns it by angle. A full turn is 256 angle units; "
          "scale is 8.8 fixed point, with 256 at life size."),
        DO("Set an affine_tileset, add an affine map to a room, then insert "
           "the Turning affine ground recipe in a Step event."),
        CHK("code_uses_effect", "An Execute Code action uses a Phase 7 effect."),
    ]),

    Topic("g_audio", "Audio", "Guides", [
        P("Two independent sound systems."),
        T(["System", "Channels", "Source"],
          [["PSG", "2 square, 1 wave, 1 noise", "registers — no RAM, no ROM"],
           ["Direct Sound", "2 PCM", "DMA from ROM, timer-clocked"]]),
        P("PSG channels cost nothing but a register write and are the right "
          "choice for effects. Direct Sound plays sampled audio, which costs "
          "ROM at its sample rate and holds a timer and a DMA channel."),
        N("A sample at 16 kHz costs 16 KB of ROM per second of audio. A minute "
          "of music sampled is a megabyte; the same minute as a pattern with "
          "instrument samples is a few kilobytes."),
    ]),

    Topic("g_sound_editor", "Writing a sound", "Guides", [
        P("A sound is a pattern of steps over three PSG channels, plus four "
          "settings that decide how it is heard."),
        T(["Channel", "Carries"],
          [["Lead", "the melody, on square 1"],
           ["Bass", "the low part, on square 2"],
           ["Drums", "kick, snare, hat and crash, on the noise channel"]]),
        P("Drums are four kinds rather than a pitch range, so they occupy the "
          "top four rows of the same roll. They stay visible while the melody "
          "is written, because the roll is one picture of the whole sound."),
        H("Sampled sound"),
        P("File \u25b8 Import Sound brings in a .wav as a sampled sound. It is "
          "converted on the way in to signed 8-bit at 16384 Hz, because the "
          "hardware has no resampler: the timer period IS the sample rate."),
        T(["Cost", "Figure"],
          [["ROM per second", "16 KB"],
           ["Longest import", "8 seconds"],
           ["Held while playing", "timer 1 and DMA 1"]]),
        P("Play Sound plays a sampled sound as a sample; its pattern tracks are "
          "ignored. rt_pcm_play mixes up to four one-shot samples at once, so "
          "a new effect does not cut the previous effect off. A fifth takes "
          "the voice closest to finishing. Playback stops each voice when its "
          "sample runs out."),
        H("Looping soundtrack"),
        P("A sampled sound marked Loop plays forever on the second PCM voice. "
          "Four mixed one-shot voices keep the first PCM voice and play over the "
          "soundtrack. rt_stop_music() silences the looping sample together "
          "with the music channels."),
        C("rt_play_music(NB_SND_THEME);\n"
          "rt_stop_music();"),
        N("Timer 1, not timer 0. Timer 0 is left for the project, so a sample "
          "and the interrupt example in lesson 13 do not collide."),
        N("A minute of music sampled is a megabyte. The same minute as a "
          "pattern with the PSG channels is a few hundred bytes. Samples are "
          "for what the PSG cannot make: a voice, a real drum, a recording."),
        DO("File \u25b8 Import Sound."),
        CHK("has_sample", "The project has a sampled sound."),

        H("Effects that need no data"),
        P("Play Sound offers twelve effects the runtime carries in code: Blip, "
          "Jump, Coin, Shoot, Hurt, Explode, Power-up, Land, Select, Error, "
          "Warp and Step. They cost no cartridge space and need no sound to "
          "have been written, so a project can make a noise before it has a "
          "single note in it."),
        C("rt_sfx(NB_SFX_COIN);   /* the same thing from code */"),

        H("The four settings"),
        T(["Setting", "Effect"],
          [["Sound", "Music replaces whatever is playing; Effect layers over it"],
           ["Width", "square wave duty: 12.5% is reedy, 50% is full, 75% hollow"],
           ["Volume", "Full, or 1 to 15"],
           ["Decay", "Hold, or Pluck 1 to 7 — how fast a note fades"]]),
        H("Reading it as notation"),
        P("Score shows the same pattern on two staves, lead on the treble and "
          "bass on the bass, with drums on a one-line staff beneath. It is a "
          "view of the pattern, not a second document: editing either changes "
          "the one sound."),
        N("Every note is one step long, because that is what the pattern "
          "holds. The staff does not invent durations the model does not "
          "carry, so it draws evenly spaced notes rather than a rhythm nobody "
          "wrote."),
        P("Bar lines fall every four steps. A note outside its staff gets "
          "ledger lines; a sharpened note shares the line of its natural and "
          "is told apart by the sharp."),

        H("Priority"),
        P("There is one wave channel, so one effect sounds at a time. A "
          "playing effect is only replaced by one of equal or higher "
          "priority, 0 to 7."),
        T(["Priority", "For"],
          [["0", "footsteps and anything repeated constantly"],
           ["1 to 3", "ordinary feedback: jumps, coins, menu clicks"],
           ["4 to 5", "things the player must not miss: damage, power-ups"],
           ["6 to 7", "deaths, explosions, the end of something"]]),
        N("Equal priority still wins, so firing the same gun twice is heard "
          "twice. Only a LOWER priority is refused."),
        N("At priority 0 every sound behaves as it did before priority "
          "existed: the newest one always wins, and a footstep can cut off a "
          "death the frame after it starts."),
        N("Sound decides whether music keeps playing. A jump or a coin set to "
          "Music silences the soundtrack for its duration; set to Effect it "
          "plays on the wave channel alongside."),
        DO("Open a sound, choose Drums, lay a beat on the top rows."),
        CHK("has_drums", "A sound has a drum track."),
        N("A sound with no drum steps emits no drum data at all, so silence "
          "costs nothing in the cartridge."),
    ]),

    Topic("g_link", "Link cable", "Guides", [
        P("The serial port operates in four modes. Multiplayer mode connects up "
          "to four units and is the one a game uses."),
        T(["Mode", "Units", "Use"],
          [["Multiplayer", "2 – 4", "16 bits from each unit per transfer"],
           ["Normal", "2", "8 or 32 bits, master/slave"],
           ["UART", "2", "byte stream"],
           ["General", "—", "the four pins as raw I/O"]]),
        N("Every unit sees every unit's word after one transfer, including its "
          "own. A transfer is initiated by the master only. Baud is 9600 to "
          "115200 bits per second, which is roughly 16 to 190 bytes per frame "
          "for the whole session, shared."),
        P("A frame of input for four players is 8 bytes. A frame of full game "
          "state is not transmissible; the shape that works is lockstep input "
          "exchange with both units simulating identically."),
        H("Sending the game itself"),
        P("File \u25b8 Export for a Link Cable builds a multiboot image: the "
          "same game linked to run from the console's own memory rather than "
          "from a cartridge, so it can be sent to a console that has no "
          "cartridge in it."),
        T(["Limit", "Value"],
          [["Size", "256 KB, the size of the memory it runs in"],
           ["Runs from", "0x2000000, not 0x8000000"],
           ["Needs", "a second console and a cable; the game does the sending"]]),
        N("A game too big to send is refused at build time rather than "
          "written. An oversized image links and produces a file that is then "
          "never sent, which looks like a cable fault."),

        H("The calls"),
        T(["Call", "Does"],
          [["rt_link_open(SIO_9600)", "once; 1 if every unit is connected"],
           ["rt_link_ready()", "every unit connected and in multiplayer mode"],
           ["rt_link_parent()", "1 on the unit that starts transfers"],
           ["rt_link_id()", "this unit's 0 to 3"],
           ["rt_link_send(word)", "latch what this unit sends next"],
           ["rt_link_start()", "parent only; a child may not start one"],
           ["rt_link_poll()", "1 when a transfer finished"],
           ["rt_link_recv(0..3)", "each unit's word; 0xFFFF where none answered"]]),
        C("if (!rt_link_ready()) return;\n"
          "rt_link_send(buttons);\n"
          "if (rt_link_parent()) rt_link_start();\n"
          "if (rt_link_poll())\n"
          "    for (int i = 0; i < 4; i++) remote[i] = rt_link_recv(i);"),
        N("Nothing here blocks. A game that waits for a transfer drops frames "
          "on a cable that is merely slow."),
        N("A failed transfer leaves the PREVIOUS words in the registers. "
          "rt_link_poll checks the error flag before reading, so a failure "
          "returns 0 rather than handing back last frame's input as though it "
          "were this frame's -- which is how two units drift apart with "
          "nothing on screen to show for it."),
        N("Two registers select the mode and both matter. Opening the link "
          "takes the port out of GPIO first; setting only the serial control "
          "register leaves an RTC cartridge's port where it was, and the link "
          "then does nothing and reports nothing."),
    ]),

    Topic("g_power_gpio", "Sleep and cartridge hardware", "Guides", [
        H("Stopping work"),
        P("rt_sleep stops the console until a button wakes it. "
          "rt_wait_vblank idles the CPU until the next interrupt, which is a "
          "one-frame wait rather than a full sleep."),
        C("if (rt_key_pressed(KEY_SELECT)) rt_sleep();\n"
          "rt_wait_vblank();"),
        T(["Call", "Function"],
          [["rt_rumble(on)", "turn the cartridge motor on or off"],
           ["rt_solar()", "read 0..255; smaller is brighter"],
           ["rt_gyro()", "read a 12-bit rate centred near 0x6C0"],
           ["rt_gpio_release()", "return the GPIO pins to the clock"]]),
        H("One cartridge device"),
        N("Rumble, solar and gyro are CARTRIDGE hardware, not parts of the "
          "console. All three share the same four GPIO pins used by the "
          "cartridge clock, so a cartridge carries at most one of them."),
        P("rt_gpio_release returns the shared pins after a hardware reading or "
          "rumble session, allowing the clock driver to claim them later."),
        DO("Add an Execute Code action that sleeps on Select or reads the one "
           "device present on the target cartridge."),
        CHK("code_calls_engine", "An Execute Code action calls an rt_ function."),
    ]),

    Topic("g_clock", "The cartridge clock", "Guides", [
        P("The real-time clock is on the CARTRIDGE, not in the console. "
          "Whether a game can tell the time depends on the cartridge it is in, "
          "which is what makes a day-night cycle a hardware question rather "
          "than a programming one."),
        C("nb_DateTime now;\n"
          "if (rt_rtc_read(&now)) {\n"
          "    night = (now.hour < 6 || now.hour >= 20);\n"
          "} else {\n"
          "    night = 0;      /* no clock: stay in daylight */\n"
          "}"),
        T(["Field", "Range"],
          [["year", "2000 to 2099"], ["month", "1 to 12"],
           ["day", "1 to 31"], ["weekday", "0 to 6"],
           ["hour", "0 to 23"], ["minute, second", "0 to 59"]]),
        N("rt_rtc_read returns 0 when there is no clock, and writes nothing. A "
          "cartridge without the chip does not answer with an error -- it "
          "answers with whatever the bus floats to, which reads as a perfectly "
          "valid 255th of the 255th. The values are checked before they are "
          "believed, so a game gets a plain no rather than a confident wrong "
          "date."),
        N("The bit-banged transfer to the clock chip has not been run against "
          "the hardware. The command encoding, the conversion from BCD and the "
          "rejection of an absent clock are covered by the runtime selftest; "
          "the transfer itself is written to the chip's published sequence."),
    ]),

    Topic("g_palette_cycles", "Palette cycling", "Guides", [
        P("rt_pal_cycle(obj, first, count, frames) rotates one contiguous "
          "background or sprite palette range and returns its cycle slot. "
          "rt_pal_cycle_stop(slot) stops that slot; -1 stops every cycle."),
        N("The room backdrop lives in background palette entry 0, and room "
          "changes rewrite it. A cycle including entry 0 therefore fights the "
          "room loader. Nothing forbids that range -- cycling the backdrop is "
          "a legitimate sky effect -- but it is the author's fight to pick "
          "and manage."),
    ]),

    Topic("g_mercy", "Mercy frames and death", "Guides", [
        P("The object field hurt_frames opts that object into invincibility "
          "after any step that costs health. Its sprite blinks while the count "
          "runs, and collision tests report nothing until it reaches zero."),
        H("Damage in the collision event"),
        C("nb_health -= 10;"),
        H("Death in the no_health event"),
        C("nb_lives -= 1;\nrt_room_goto(NB_ROOM_START);"),
        P("The no_health event fires once when health reaches zero. It re-arms "
          "after health rises above zero, so the event needs no latch variable."),
        DO("Set hurt_frames on the player object, subtract health in its "
           "collision event, and place death logic in its no_health event."),
    ]),

    Topic("g_saves", "Saving", "Guides", [
        H("Save type"),
        P("The project-level save_type setting accepts sram, flash64, "
          "flash128, eeprom512 or eeprom8k. The generator emits the matching "
          "emulator save signature. Author code still uses rt_game_save and "
          "rt_game_load unchanged."),
        T(["Type", "Capacity"],
          [["sram", "32 KB"], ["flash64", "64 KB"],
           ["flash128", "128 KB"], ["eeprom512", "512 bytes"],
           ["eeprom8k", "8 KB"]]),
        C("rt_game_save();\n"
          "if (rt_game_load()) loaded = 1;"),
        H("EEPROM"),
        P("EEPROM changes the cartridge save hardware, not the author API. "
          "Its serial device transfers 8-byte blocks through DMA3; the runtime "
          "keeps that protocol behind rt_game_save and rt_game_load."),
        P("SRAM provides 32 KB of cartridge save memory at 0xE000000."),
        N("SRAM is 8-bit only. Reading or writing a u16 or u32 there returns or "
          "stores the wrong bytes. Every access is one byte at a time, which "
          "also means a struct cannot be copied into it wholesale."),
        P("The engine saves score, lives, health and the 32 global variables. "
          "Anything else has to be written byte by byte."),
        N("An emulator infers save type from strings present in the ROM. A ROM "
          "with no recognisable save signature saves nothing and reports "
          "nothing. The build writes the signature."),
        DO("Choose a save_type in project settings and call rt_game_save from "
           "the event that commits progress."),
        CHK("code_calls_engine", "An Execute Code action calls an rt_ function."),
    ]),
]


# ===========================================================================
# DERIVED REFERENCE
# ===========================================================================
def _param_kind(kind):
    """Human wording for an action parameter's type."""
    if kind == "int":
        return "number"
    if kind == "str":
        return "text"
    if kind == "code":
        return "C"
    if kind == "obj":
        return "an object in the project"
    if kind == "room":
        return "a room in the project"
    if kind == "spr":
        return "a sprite in the project"
    if kind == "snd":
        return "a sound in the project"
    if isinstance(kind, (list, tuple)):
        return " / ".join(str(x) for x in kind)
    return str(kind)


def reference_actions(action_groups, action_defs, action_tips,
                      container=(), presets=None):
    """One topic per action group, derived from the tables the app itself uses.

    Written by hand this would be 45 entries that drift from the tool within a
    month. Derived, the reference cannot disagree with the palette."""
    params = {a[0]: a[2] for a in action_defs}
    labels = {a[0]: a[1] for a in action_defs}
    topics = []
    for group, keys in action_groups:
        body = [P("Actions in the %s group of the palette. Every one of them "
                  "compiles to C that Show C displays." % group.title())]
        for k in keys:
            if k not in labels and k in (presets or {}):
                # A PRESET: a palette entry that inserts a block of C
                # rather than its own action kind, so saved projects
                # carry only kinds the generator already knows. It is
                # still something an author can click, so the reference
                # owes it an entry -- the palette-coverage gate exists
                # to notice exactly this gap.
                body.append(H(presets[k][0]))
                tip = action_tips.get(k)
                if tip:
                    body.append(P(tip + "."))
                body.append(C(presets[k][1]))
                continue
            if k not in labels:
                continue
            body.append(H(labels[k]))
            tip = action_tips.get(k)
            if tip:
                body.append(P(tip + "."))
            ps = params.get(k) or []
            if ps:
                body.append(T(["Parameter", "Accepts"],
                              [[p[1], _param_kind(p[2])] for p in ps]))
            else:
                body.append(P("No parameters."))
            if k in container:
                body.append(P("Holds actions beneath it, which run only when "
                              "the condition holds."))
        topics.append(Topic("act_" + group.lower(), group.title(),
                            "Actions", body))
    return topics


_DECL = re.compile(
    r"^\s*(?:extern\s+)?"
    r"((?:const\s+)?(?:unsigned\s+)?[A-Za-z_][A-Za-z0-9_]*\s*\**)\s+"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*\(([^;{]*)\)\s*;"
    r"\s*(?:/\*(?P<note>.*?)\*/|//(?P<note2>.*))?", re.M)


def _paragraphs(lines):
    """Banner-comment lines folded into paragraphs on blank lines.

    Code samples inside a banner (indented lines) are dropped rather than run
    together with the prose: a wrapped line of C in a paragraph reads as
    nonsense, and the same code is on the declaration below it."""
    out, buf = [], []
    for raw in lines:
        t = raw.strip()
        if not t:
            if buf:
                out.append(" ".join(buf))
                buf = []
            continue
        if t.startswith(("#", "{", "}")) or t.endswith((";", "{")):
            continue
        buf.append(t)
    if buf:
        out.append(" ".join(buf))
    return [t for t in out if len(t) > 24]


def _clean_section(raw):
    """A banner comment's text as a heading.

    The headers mark work by phase ("interrupts (Phase 6)"). That is a note to
    whoever is building the SDK; to whoever is reading the reference it is a
    number with no referent."""
    t = raw.strip().rstrip("-").strip()
    t = re.sub(r"\s*\((?:phase|step)\s*\d+\)\s*$", "", t, flags=re.I)
    t = t.strip(" -")
    return (t[:1].upper() + t[1:]) if t else "Reference"


def reference_engine(header_path=None):
    """Parse runtime.h into a reference, keeping the comment above each call.

    The comments in that header are already written for a reader — they carry
    units, costs and constraints. Copying them into a separate document is how
    two descriptions of one function come to disagree."""
    path = header_path or os.path.join(RUNTIME_DIR, "runtime.h")
    try:
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
    except Exception:
        return []

    # Section headings in the header are "/* --- name --- */" or "/* ---- name --"
    sections = []
    cur = ("Engine", [], [])
    lines = src.split("\n")
    i = 0
    pending = []          # comment lines gathered above a declaration
    while i < len(lines):
        line = lines[i]
        head = re.match(r"\s*/\*\s*-{2,}\s*(.+?)\s*-{2,}", line)
        if head:
            if cur[1] or cur[2]:
                sections.append(cur)
            # The banner under a heading is not decoration: for timers, DMA and
            # the BIOS calls it IS the documentation, and discarding it left a
            # reference of bare signatures. Keep it as the section's opening.
            intro = []
            first = re.sub(r"^\s*/\*\s*-{2,}.*?-{2,}", "", line).strip()
            if first and "*/" not in first:
                intro.append(first)
            while i < len(lines) and "*/" not in lines[i]:
                i += 1
                if i < len(lines):
                    t = lines[i].strip().lstrip("*").strip()
                    if "*/" in t:
                        t = t.split("*/")[0].strip()
                    intro.append(t)
            cur = (_clean_section(head.group(1)), [], _paragraphs(intro))
            pending = []
            i += 1
            continue
        # A declaration wrapped across lines is still one declaration. Read
        # line by line it matches nothing and vanishes from the reference
        # silently -- which is how rt_menu_open_var came to be declared,
        # implemented, used by an action, and absent from the only
        # documentation on the machine.
        probe = line
        if ("(" in line and ";" not in line
                and not line.lstrip().startswith(("*", "/"))):
            j = i
            while j + 1 < len(lines) and ";" not in probe and j - i < 6:
                j += 1
                probe += " " + lines[j].strip()
            if _DECL.match(probe):
                i = j
                line = probe
        m = _DECL.match(line)
        if m:
            ret, name, args = m.group(1).strip(), m.group(2), m.group(3).strip()
            sig = "%s %s(%s);" % (ret, name, args or "void")
            # A note may sit above the declaration or trail it on the same line.
            # The trailing form is where this header states units and ranges
            # ("0 .. n-1", "pixels per frame"), so dropping it drops the part of
            # the reference that answers the question actually being asked.
            tail = (m.group("note") or m.group("note2") or "").strip()
            above = " ".join(t.strip(" *") for t in pending).strip()
            note = " - ".join(x for x in (above, tail) if x)
            if not note and name == "rt_window_obj_off":
                note = "disable the sprite-shaped object window"
            if not note and name == "rt_pal_get":
                note = "read one background or sprite palette colour"
            if not note and name == "rt_pal_load":
                note = "load a run of background or sprite palette colours"
            cur[1].append((sig, note))
            pending = []
        elif line.strip().startswith(("/*", "*", "//")):
            t = line.strip().lstrip("/*").lstrip("*").lstrip("/").strip()
            if t and not set(t) <= set("-= "):
                pending.append(t)
        elif not line.strip():
            pending = []
        i += 1
    if cur[1] or cur[2]:
        sections.append(cur)

    topics = []
    for name, calls, intro in sections:
        body = [P(t) for t in intro]
        for sig, note in calls:
            body.append(C(sig))
            if note:
                # Header comments are written as fragments trailing a
                # declaration ("speed in whole pixels per frame"). Standing on
                # their own under a signature they have to be sentences.
                note = note[:1].upper() + note[1:]
                body.append(P(note if note.endswith((".", ")")) else note + "."))
        if body:
            tid = "eng_" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
            topics.append(Topic(tid, name, "Engine calls", body))
    return topics


def reference_registers(header_path=None):
    """Every hardware name gba.h defines, grouped by its banner comment."""
    path = header_path or os.path.join(RUNTIME_DIR, "gba.h")
    try:
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
    except Exception:
        return []
    groups = []
    cur = ("Registers", [])
    for line in src.split("\n"):
        head = re.match(r"\s*/\*\s*-{2,}\s*(.+?)\s*-{2,}", line)
        if head:
            if cur[1]:
                groups.append(cur)
            cur = (_clean_section(head.group(1)), [])
            continue
        m = re.match(r"\s*#define\s+([A-Za-z_][A-Za-z0-9_]*)\s+(.+?)\s*(?:/\*(.*?)\*/)?\s*$",
                     line)
        if m:
            name, val, note = m.group(1), m.group(2).strip(), (m.group(3) or "").strip()
            if len(val) > 46:
                val = val[:44] + "…"
            cur[1].append([name, val, note])
    if cur[1]:
        groups.append(cur)
    topics = []
    for name, rows in groups:
        tid = "reg_" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        topics.append(Topic(tid, name[:1].upper() + name[1:], "Hardware",
                            [T(["Name", "Value", "Note"], rows)]))
    return topics


# ===========================================================================
# CHECKPOINTS — verified against the open project
# ===========================================================================
def _all_actions(proj):
    """Every action in the project, including nested ones."""
    out = []

    def walk(acts):
        for a in acts or []:
            if isinstance(a, dict):
                out.append(a)
                walk(a.get("children"))

    for o in (proj or {}).get("objects") or []:
        for ev in o.get("events") or []:
            walk(ev.get("actions"))
    return out


def _code_blocks(proj):
    return [str(a.get("code") or "") for a in _all_actions(proj)
            if a.get("kind") == "execute_code"]


def _strip_comments(code):
    code = re.sub(r"/\*.*?\*/", " ", code, flags=re.S)
    return re.sub(r"//[^\n]*", " ", code)


CHECKS = {}


def _check(name):
    def deco(fn):
        CHECKS[name] = fn
        return fn
    return deco


@_check("has_object")
def _c_has_object(proj):
    n = len((proj or {}).get("objects") or [])
    return n > 0, "%d in the project" % n


@_check("has_event")
def _c_has_event(proj):
    n = sum(len(o.get("events") or [])
            for o in (proj or {}).get("objects") or [])
    return n > 0, "%d event%s in the project" % (n, "" if n == 1 else "s")


@_check("has_action")
def _c_has_action(proj):
    n = len(_all_actions(proj))
    return n > 0, "%d action%s in the project" % (n, "" if n == 1 else "s")


@_check("has_script")
def _c_has_script(proj):
    n = len([x for x in ((proj or {}).get("scripts") or [])
             if str((x or {}).get("code") or "").strip()])
    return n > 0, "%d script%s in the project" % (n, "" if n == 1 else "s")


@_check("code_declares_var")
def _c_declares_var(proj):
    pat = re.compile(r"\b(?:u8|s8|u16|s16|u32|s32|int|char|short|long|var)\s+"
                     r"[A-Za-z_][A-Za-z0-9_]*")
    for code in _code_blocks(proj):
        if pat.search(_strip_comments(code)):
            return True, "found"
    return False, "no declaration in any Execute Code action"


@_check("code_has_if")
def _c_has_if(proj):
    for code in _code_blocks(proj):
        if re.search(r"\bif\s*\(", _strip_comments(code)):
            return True, "found"
    return False, "no if in any Execute Code action"


@_check("code_has_loop")
def _c_has_loop(proj):
    for code in _code_blocks(proj):
        if re.search(r"\b(?:for|while)\s*\(", _strip_comments(code)):
            return True, "found"
    return False, "no for or while in any Execute Code action"


@_check("code_calls_engine")
def _c_calls_engine(proj):
    for code in _code_blocks(proj):
        m = re.search(r"\b(rt_[A-Za-z0-9_]+)\s*\(", _strip_comments(code))
        if m:
            return True, "calls %s" % m.group(1)
    return False, "no rt_ call in any Execute Code action"


@_check("has_tilecol")
def _c_has_tilecol(proj):
    n = len([o for o in ((proj or {}).get("objects") or [])
             if (o or {}).get("tilecol")])
    return n > 0, "%d object%s" % (n, "" if n == 1 else "s")


@_check("has_sample")
def _c_has_sample(proj):
    n = len([s for s in ((proj or {}).get("sounds") or [])
             if len((s or {}).get("pcm") or []) >= 16])
    return n > 0, "%d sampled sound%s" % (n, "" if n == 1 else "s")


@_check("has_drums")
def _c_has_drums(proj):
    n = len([s for s in ((proj or {}).get("sounds") or [])
             if any((s or {}).get("drum") or [])])
    return n > 0, "%d sound%s with drums" % (n, "" if n == 1 else "s")


@_check("has_autotile")
def _c_has_autotile(proj):
    n = len([t for t in ((proj or {}).get("tilesets") or [])
             if isinstance((t or {}).get("auto_base"), int)])
    return n > 0, "%d run%s" % (n, "" if n == 1 else "s")


@_check("has_warp")
def _c_has_warp(proj):
    n = 0
    for r in (proj or {}).get("rooms") or []:
        n += len([w for w in ((r or {}).get("warps") or [])
                  if (w or {}).get("room")])
    return n > 0, "%d door%s" % (n, "" if n == 1 else "s")


@_check("has_dialogue")
def _c_has_dialogue(proj):
    n = len([a for a in _all_actions(proj)
             if a.get("kind") == "say" and str(a.get("text") or "").strip()])
    return n > 0, "%d message%s" % (n, "" if n == 1 else "s")


@_check("uses_glide")
def _c_uses_glide(proj):
    n = len([a for a in _all_actions(proj) if a.get("kind") == "glide"])
    for code in _code_blocks(proj):
        if re.search(r"\brt_glide\s*\(", _strip_comments(code)):
            n += 1
    return n > 0, "%d glide%s" % (n, "" if n == 1 else "s")


@_check("uses_menu")
def _c_uses_menu(proj):
    # Both ways round, like uses_glide. This counted only `rt_menu_open(` in
    # code, so the lesson could be finished by writing C and NOT by adding a
    # Show Menu action — which is the way the tool is meant to be used first,
    # and what the checkpoint's own words promise. The action was added after
    # the checkpoint and never reached it.
    n = len([a for a in _all_actions(proj) if a.get("kind") == "menu"])
    blocks = list(_code_blocks(proj))
    blocks += [str((s or {}).get("code") or "")
               for s in ((proj or {}).get("scripts") or [])]
    for code in blocks:
        if re.search(r"\brt_menu_open\s*\(", _strip_comments(code)):
            n += 1
    return n > 0, "%d menu%s" % (n, "" if n == 1 else "s")


@_check("has_table")
def _c_has_table(proj):
    n = len([t for t in ((proj or {}).get("tables") or [])
             if (t or {}).get("rows")])
    return n > 0, "%d table%s" % (n, "" if n == 1 else "s")


@_check("has_solid_tile")
def _c_has_solid_tile(proj):
    n = 0
    for ts in (proj or {}).get("tilesets") or []:
        n += sum(1 for v in ((ts or {}).get("solid") or []) if v)
    return n > 0, "%d solid tile%s" % (n, "" if n == 1 else "s")


@_check("code_uses_effect")
def _c_uses_effect(proj):
    pat = re.compile(r"\brt_(blend_alpha|blend_brightness|window|mosaic|"
                     r"bg_affine|obj_affine)\s*\(")
    for code in _code_blocks(proj):
        m = pat.search(_strip_comments(code))
        if m:
            return True, "calls rt_%s" % m.group(1)
    return False, "no blend, window, mosaic or affine call"


@_check("code_writes_register")
def _c_writes_reg(proj):
    for code in _code_blocks(proj):
        m = re.search(r"\b(REG_[A-Za-z0-9_]+)\s*(?:=|\|=|&=|\^=)",
                      _strip_comments(code))
        if m:
            return True, "writes %s" % m.group(1)
    return False, "no register written in any Execute Code action"


def run_check(check_id, proj):
    """(passed, detail). An unknown id is not a failure — a lesson naming a
    check that no longer exists must not read as work left undone."""
    fn = CHECKS.get(check_id)
    if fn is None:
        return None, ""
    try:
        return fn(proj or {})
    except Exception as exc:            # a checkpoint must never break the pane
        return False, str(exc)


def course_progress(proj):
    """(done, total) over every checkpoint in the course."""
    done = total = 0
    for t in COURSE:
        for kind, v in t.body:
            if kind != "check":
                continue
            total += 1
            ok, _ = run_check(v[0], proj)
            if ok:
                done += 1
    return done, total


# ===========================================================================
# THE PANE
# ===========================================================================
CSS = b"""
.helpnav { background: #F4F2EC; }
.helpnav list { background: transparent; }
.helpsec { font-size: 10px; letter-spacing: 1px; color: #8A857A;
           padding: 12px 12px 4px 12px; }
.helprow { padding: 5px 12px; font-size: 12px; color: #2A2620; }
.helprow:selected, .helpnav list row:selected { background-image: none;
           background-color: #C8341E; color: #FCFBF8; }
.helpbody { background: #FCFBF8; }
.helptitle { font-size: 20px; font-weight: bold; color: #1A1916; }
.helph { font-size: 14px; font-weight: bold; color: #1A1916; }
.helpp { font-size: 13px; color: #3A362E; }
.helpcode { font-family: monospace; font-size: 12px; color: #1A1916;
            background: #F4F2EC; padding: 10px 12px; }
.helpnote { font-size: 12px; color: #3A362E; background: #F4F2EC;
            padding: 9px 11px; border-left: 3px solid #C8341E; }
.helpdo { font-size: 13px; color: #1A1916; }
.helpdone { color: #4F7A3A; font-size: 12px; }
.helptodo { color: #8A857A; font-size: 12px; }
.helpth { font-size: 11px; font-weight: bold; color: #6E695E; }
.helptd { font-size: 12px; color: #3A362E; }
.helpprog { font-size: 11px; color: #8A857A; padding: 6px 12px; }
.helpsearch { font-size: 12px; }
.helprule { background: #D7D2C5; }
.helpinsert { background: #FCFBF8; color: #1A1916; border: 1px solid #C9C4B6;
              border-radius: 8px; padding: 5px 14px; font-size: 12px;
              font-weight: 600; box-shadow: none; }
.helpinsert:hover { background: #F1EEE6; }
.helpinsert:disabled { color: #B3AD9E; }
"""


class HelpPane(Gtk.Box):
    """Contents on the left, one topic on the right, search over both.

    get_project is a callable rather than a project, because the pane outlives
    any one project and a checkpoint has to read the state at the moment it is
    displayed."""

    def __init__(self, get_project=None, topics=None, on_insert=None):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._get_project = get_project or (lambda: {})
        # None means the host cannot take code -- the button then says so
        # rather than being drawn live and doing nothing when pressed.
        self._on_insert = on_insert
        self.topics = list(topics if topics is not None else all_topics())
        self._by_id = {t.tid: t for t in self.topics}
        self._current = None

        nav = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        nav.get_style_context().add_class("helpnav")
        nav.set_size_request(206, -1)

        se = Gtk.SearchEntry()
        se.set_placeholder_text("Search")
        se.get_style_context().add_class("helpsearch")
        se.set_margin_start(10)
        se.set_margin_end(10)
        se.set_margin_top(10)
        se.set_margin_bottom(6)
        se.connect("search-changed", self._on_search)
        nav.pack_start(se, False, False, 0)
        self._search = se

        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._list.connect("row-selected", self._on_row)
        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sc.add(self._list)
        nav.pack_start(sc, True, True, 0)

        self._prog = Gtk.Label(xalign=0.0)
        self._prog.get_style_context().add_class("helpprog")
        nav.pack_start(self._prog, False, False, 0)

        self.pack_start(nav, False, False, 0)
        rule = Gtk.Box()
        rule.get_style_context().add_class("helprule")
        rule.set_size_request(1, -1)
        self.pack_start(rule, False, False, 0)

        self._body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._body.get_style_context().add_class("helpbody")
        bsc = Gtk.ScrolledWindow()
        bsc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        bsc.add(self._body)
        self._bsc = bsc
        self.pack_start(bsc, True, True, 0)

        self._fill_list(self.topics)
        if self.topics:
            self.show_topic(self.topics[0].tid)

    # -- navigation ----------------------------------------------------------
    def _fill_list(self, topics):
        for ch in self._list.get_children():
            self._list.remove(ch)
        section = None
        for t in topics:
            if t.section != section:
                section = t.section
                row = Gtk.ListBoxRow()
                row.set_selectable(False)
                row.set_activatable(False)
                lab = Gtk.Label(label=section.upper(), xalign=0.0)
                lab.get_style_context().add_class("helpsec")
                row.add(lab)
                self._list.add(row)
            row = Gtk.ListBoxRow()
            row.tid = t.tid
            lab = Gtk.Label(label=t.title, xalign=0.0)
            lab.get_style_context().add_class("helprow")
            lab.set_ellipsize(Pango.EllipsizeMode.END)
            row.add(lab)
            self._list.add(row)
        self._list.show_all()

    def _on_search(self, entry):
        q = entry.get_text().strip().lower()
        if not q:
            self._fill_list(self.topics)
            return
        hits = [t for t in self.topics if q in t.text().lower()]
        self._fill_list(hits or [])
        if not hits:
            self._render_empty(q)

    def _on_row(self, _list, row):
        if row is not None and getattr(row, "tid", None):
            self.show_topic(row.tid)

    def show_topic(self, tid):
        t = self._by_id.get(tid)
        if t is None:
            return
        self._current = tid
        self._render(t)
        adj = self._bsc.get_vadjustment()
        if adj:
            adj.set_value(0)

    # -- rendering -----------------------------------------------------------
    def _clear_body(self):
        for ch in self._body.get_children():
            self._body.remove(ch)

    def _render_empty(self, q):
        self._clear_body()
        lab = Gtk.Label(label="No topic contains “%s”." % q, xalign=0.0)
        lab.get_style_context().add_class("helpp")
        lab.set_margin_start(26)
        lab.set_margin_top(26)
        self._body.pack_start(lab, False, False, 0)
        self._body.show_all()

    def _label(self, text, style, top=0, bottom=0):
        lab = Gtk.Label(label=text, xalign=0.0)
        lab.get_style_context().add_class(style)
        lab.set_line_wrap(True)
        lab.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        # A wrapping label with no width cap grows the pane instead of wrapping.
        lab.set_max_width_chars(66)
        lab.set_margin_start(26)
        lab.set_margin_end(26)
        lab.set_margin_top(top)
        lab.set_margin_bottom(bottom)
        return lab

    def _render(self, topic):
        self._clear_body()
        proj = {}
        try:
            proj = self._get_project() or {}
        except Exception:
            proj = {}

        self._body.pack_start(self._label(topic.title, "helptitle", 24, 12),
                              False, False, 0)

        for kind, v in topic.body:
            if kind == "h":
                self._body.pack_start(self._label(v, "helph", 16, 4),
                                      False, False, 0)
            elif kind == "p":
                self._body.pack_start(self._label(v, "helpp", 0, 9),
                                      False, False, 0)
            elif kind == "note":
                lab = self._label(v, "helpnote", 6, 10)
                self._body.pack_start(lab, False, False, 0)
            elif kind == "code":
                self._body.pack_start(self._code_view(v), False, False, 0)
            elif kind == "list":
                for item in v:
                    self._body.pack_start(self._label("•   " + item, "helpp", 0, 5),
                                          False, False, 0)
            elif kind == "table":
                self._body.pack_start(self._table(v[0], v[1]), False, False, 0)
            elif kind == "do":
                self._body.pack_start(self._label("▸   " + v, "helpdo", 8, 4),
                                      False, False, 0)
            elif kind == "check":
                self._body.pack_start(self._checkline(v[0], v[1], proj),
                                      False, False, 0)
            elif kind == "insert":
                self._body.pack_start(self._insert_row(v[1], v[2]),
                                      False, False, 0)

        self._body.pack_start(Gtk.Box(), False, False, 12)
        self._update_progress(proj)
        self._body.show_all()

    def _code_view(self, code):
        """Monospace, horizontally scrollable. Code must not wrap: a wrapped
        line of C reads as two statements."""
        lab = Gtk.Label(label=code, xalign=0.0)
        lab.get_style_context().add_class("helpcode")
        lab.set_selectable(True)
        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        sc.add(lab)
        # The height of a label inside a scroller is not requested by the
        # scroller, so it has to be asked for or the block collapses to nothing.
        lines = code.count("\n") + 1
        sc.set_size_request(-1, 20 + lines * 17)
        sc.set_margin_start(26)
        sc.set_margin_end(26)
        sc.set_margin_bottom(11)
        return sc

    def _table(self, head, rows):
        grid = Gtk.Grid()
        grid.set_column_spacing(18)
        grid.set_row_spacing(5)
        grid.set_margin_start(26)
        grid.set_margin_end(26)
        grid.set_margin_top(2)
        grid.set_margin_bottom(12)
        for c, h in enumerate(head):
            lab = Gtk.Label(label=h.upper(), xalign=0.0)
            lab.get_style_context().add_class("helpth")
            grid.attach(lab, c, 0, 1, 1)
        for r, row in enumerate(rows):
            for c, cell in enumerate(row):
                lab = Gtk.Label(label=str(cell), xalign=0.0)
                lab.get_style_context().add_class("helptd")
                lab.set_line_wrap(True)
                lab.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
                lab.set_max_width_chars(34)
                grid.attach(lab, c, r + 1, 1, 1)
        return grid

    def _checkline(self, check_id, text, proj):
        ok, detail = run_check(check_id, proj)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_start(26)
        box.set_margin_end(26)
        box.set_margin_top(2)
        box.set_margin_bottom(10)
        mark = Gtk.Label(label="✓" if ok else "○", xalign=0.0)
        mark.get_style_context().add_class("helpdone" if ok else "helptodo")
        box.pack_start(mark, False, False, 0)
        lab = Gtk.Label(label=text + ("  —  " + detail if detail else ""),
                        xalign=0.0)
        lab.get_style_context().add_class("helpdone" if ok else "helptodo")
        lab.set_line_wrap(True)
        lab.set_max_width_chars(60)
        box.pack_start(lab, False, False, 0)
        return box

    def _insert_row(self, code, scope="event"):
        """Put this recipe into the selected event as an Execute Code action.

        Insertion rather than retyping is the point: what is being taught is
        modifying working code, and a transcription error in the first five
        minutes teaches only that the machine is hostile."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_start(26)
        box.set_margin_end(26)
        box.set_margin_bottom(14)
        btn = Gtk.Button(label="Add to This Event" if scope == "event"
                         else "Add as a Script")
        btn.get_style_context().add_class("helpinsert")
        if self._on_insert is None:
            btn.set_sensitive(False)
            btn.set_tooltip_text("Open an object event to insert into")
        else:
            btn.connect("clicked", lambda *_: self._do_insert(code, scope))
        box.pack_start(btn, False, False, 0)
        self._insert_note = Gtk.Label(label="", xalign=0.0)
        self._insert_note.get_style_context().add_class("helpdone")
        box.pack_start(self._insert_note, False, False, 0)
        return box

    def _do_insert(self, code, scope="event"):
        try:
            placed = self._on_insert(code, scope)
        except Exception as exc:                            # noqa: BLE001
            placed = False
            self._insert_note.get_style_context().add_class("helptodo")
            self._insert_note.set_text(str(exc)[:70])
            return
        # A button that reports nothing is a button that looks broken when it
        # worked and worked when it was refused.
        self._insert_note.set_text(_t("Added") if placed
                                   else _t("Select an event first"))

    def _update_progress(self, proj):
        done, total = course_progress(proj)
        # Translate the pattern, then fill it: nbi18n patches the setters and
        # would be handed "Course  5 / 7", which is in no catalog.
        self._prog.set_text((_t("Course  %d / %d") % (done, total)) if total
                            else "")

    def refresh(self):
        """Re-read the project. Called when the project changes so a checkpoint
        marked done is marked done without reopening the topic."""
        if self._current:
            self.show_topic(self._current)


# ---------------------------------------------------------------------------
def all_topics(tables=None):
    """The whole book, in reading order.

    tables lets a caller pass the action palette explicitly; without it the
    palette is read from gbasdk, imported here rather than at module scope
    because gbasdk imports this module."""
    if tables is None:
        try:
            import gbasdk
            tables = {
                "action_groups": gbasdk.ACTION_GROUPS,
                "action_defs": gbasdk.ACTION_DEFS,
                "action_tips": gbasdk.ACTION_TIPS,
                "container": gbasdk.CONTAINER_ACTIONS,
                "presets": gbasdk.ACTION_PRESETS,
            }
        except Exception:
            tables = {}
    out = list(COURSE) + recipe_topics()
    if tables:
        out += reference_actions(tables.get("action_groups", ()),
                                 tables.get("action_defs", ()),
                                 tables.get("action_tips", {}),
                                 tables.get("container", ()),
                                 tables.get("presets", {}))
    out += reference_engine()
    out += list(GUIDES)
    out += reference_registers()
    return out
