#!/usr/bin/env python3
"""
gbabuild — turn a Notebook OS GBA SDK game model into a real .gba ROM.

The model (the IDE's project JSON) describes sprites, objects (with Game-Maker
style events made of drag-and-drop actions), and rooms. generate_c() emits a
single game_data.c against the runtime in /opt/notebook/gbaruntime; build_rom()
compiles it with the shipped arm-none-eabi-gcc, links with the runtime + crt0,
objcopy's to a binary and fixes the GBA header checksum. The output ROM runs in
the GBA Emulator.

Nothing here trusts model strings as code: every numeric value is parsed to an
int, and every identifier is sanitised to a C token, so a project can never
inject arbitrary C.
"""
import os

try:                                    # the build messages are what an author
    from nbi18n import _t               # reads when their game does not work,
except Exception:                       # so they are translated like any other
    def _t(s):                          # UI text. Falls back to English if the
        return s                        # catalogs are not importable.
import re
import json
import shutil
import signal
import subprocess

# Where the runtime + toolchain live on the guest.
RUNTIME_DIR = "/opt/notebook/gbaruntime"
TOOLCHAIN_DIR = "/opt/gba-toolchain"       # ships the xpack arm-none-eabi-gcc

TRANSPARENT = 0x7C1F

# GM-style key names -> runtime key macro.
KEY_MACRO = {
    "left": "KEY_LEFT", "right": "KEY_RIGHT", "up": "KEY_UP", "down": "KEY_DOWN",
    "a": "KEY_A", "b": "KEY_B", "start": "KEY_START", "select": "KEY_SELECT",
    "l": "KEY_L", "r": "KEY_R",
}
CMP_OP = {"==": "==", "!=": "!=", "<": "<", ">": ">", "<=": "<=", ">=": ">=",
          "=": "=="}
# The twelve effects the runtime carries with no data at all. Play Sound only
# ever offered the project's OWN sounds, so a new project -- which has none --
# could not make a noise until someone had written a tune, which is a long way
# from a first jump.
BUILTIN_SFX = (
    ("sfx:blip", "Blip", "NB_SFX_BLIP"),
    ("sfx:jump", "Jump", "NB_SFX_JUMP"),
    ("sfx:coin", "Coin", "NB_SFX_COIN"),
    ("sfx:shoot", "Shoot", "NB_SFX_SHOOT"),
    ("sfx:hurt", "Hurt", "NB_SFX_HURT"),
    ("sfx:explode", "Explode", "NB_SFX_EXPLODE"),
    ("sfx:powerup", "Power-up", "NB_SFX_POWERUP"),
    ("sfx:land", "Land", "NB_SFX_LAND"),
    ("sfx:select", "Select", "NB_SFX_SELECT"),
    ("sfx:error", "Error", "NB_SFX_ERROR"),
    ("sfx:warp", "Warp", "NB_SFX_WARP"),
    ("sfx:step", "Step", "NB_SFX_STEP"),
)
SFX_MACRO = {k: m for k, _lbl, m in BUILTIN_SFX}

DIR_SPEED = {   # direction -> (hx, vy) multipliers
    "left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1),
    "upleft": (-1, -1), "upright": (1, -1), "downleft": (-1, 1),
    "downright": (1, 1), "stop": (0, 0),
}


# ---------------------------------------------------------------- helpers
def _cid(s, prefix="id"):
    """A safe C identifier from an arbitrary model id."""
    s = re.sub(r"[^A-Za-z0-9_]", "_", str(s or ""))
    if not s or not (s[0].isalpha() or s[0] == "_"):
        s = prefix + "_" + s
    return s


#: The widest integer the target can hold. Every number this generator emits
#: ends up in an s32 on an ARM7TDMI, so a value outside this range is not a
#: number the hardware has — it wraps, silently, with no warning from the
#: compiler and nothing in the build report. Text that is not a number at all
#: already becomes the default; a number too large should not be treated more
#: leniently than a typo.
S32_MIN, S32_MAX = -(1 << 31), (1 << 31) - 1


def _int(v, default=0):
    try:
        if isinstance(v, bool):
            n = int(v)
        elif isinstance(v, (int, float)):
            n = int(v)
        else:
            n = int(str(v).strip())
    except (ValueError, TypeError):
        return default
    return max(S32_MIN, min(S32_MAX, n))


def _rgb15(color, default=0):
    """A model colour (#rrggbb, 0xBGR555 int, or [r,g,b] 0-255) -> BGR555."""
    if isinstance(color, int):
        return color & 0x7FFF
    if isinstance(color, (list, tuple)) and len(color) == 3:
        r, g, b = (max(0, min(255, _int(c))) for c in color)
        return (r >> 3) | ((g >> 3) << 5) | ((b >> 3) << 10)
    s = str(color or "").strip()
    if s.startswith("#") and len(s) == 7:
        r = int(s[1:3], 16); g = int(s[3:5], 16); b = int(s[5:7], 16)
        return (r >> 3) | ((g >> 3) << 5) | ((b >> 3) << 10)
    return default & 0x7FFF


def _cstr(s):
    """A model string -> a safe C string literal (printable ASCII only)."""
    out = ['"']
    for ch in str(s or ""):
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif 32 <= ord(ch) < 127:
            out.append(ch)
        else:
            # OCTAL, not \xNN. A C hex escape consumes every hex digit that
            # follows it, so "caf\xe9" + "2" is read as one escape \xe92 --
            # "hex escape sequence out of range", and the text silently loses
            # a character. An octal escape stops after three digits, so
            # "caf\3512" is \351 followed by "2". Author dialogue with an
            # accent in it is not an edge case in a tool meant for writing
            # games in any language.
            out.append("\\%03o" % (ord(ch) & 0xFF))
    out.append('"')
    return "".join(out)


class BuildError(Exception):
    pass


class GmlError(Exception):
    """A mistake in a user's script. `line` is the 1-based line it was found on
    (0 when the position is unknown) so the IDE can point at it."""

    def __init__(self, message, line=0):
        super().__init__(message)
        self.line = line


# ------------------------------------------------------------ script compiler
# The scripting language is a curated SUBSET OF C, not a separate language —
# see Part 0 of docs/GBA-SDK-SPEC.md. Levels 2 and 3 are therefore one
# language: dropping from script into hand-written C is adding, not switching,
# and the Help's "C for GBA" chapter is also the scripting manual.
# Renamed from a GML_ prefix when the language became a C subset.
#: Alarms per instance. Mirrors NB_MAX_ALARMS in the runtime header, which is
#: the authority; tools/gbaruntime_selftest.py fails if the two drift apart.
MAX_ALARMS = 4

SCRIPT_BUILTIN_VARS = {"x", "y", "hspeed", "vspeed", "image_index", "image_speed",
                    "grav"}
SCRIPT_GLOBALS = {"score": "nb_score", "lives": "nb_lives", "health": "nb_health"}
SCRIPT_KEYS = {"vk_left": "KEY_LEFT", "vk_right": "KEY_RIGHT", "vk_up": "KEY_UP",
            "vk_down": "KEY_DOWN", "vk_a": "KEY_A", "vk_b": "KEY_B",
            "vk_start": "KEY_START", "vk_select": "KEY_SELECT", "vk_l": "KEY_L",
            "vk_r": "KEY_R"}
#: Words this language does not have, and what to write instead. Each one is
#: something a C, JavaScript or Python habit reaches for first.
SCRIPT_NOT_HERE = {
    "for": "this language counts with repeat (3) { } or loops with "
           "while (x < 3) { }",
    "do": "this language loops with while (x < 3) { } or repeat (3) { }",
    "switch": "use if and else",
    "case": "use if and else",
    "break": "a while loop ends when its test is false; exit leaves the event",
    "continue": "use if to skip the part that should not run",
    "function": "a Script resource holds a function; write it there",
    "def": "a Script resource holds a function; write it there",
    "elif": "write else if",
    "print": "there is no console on a Game Boy Advance; use Say to show text",
}

SCRIPT_KEYWORDS = {"if", "else", "while", "repeat", "exit", "var", "true", "false",
                "return", "then", "begin", "end"}


class _Gml:
    """A small recursive-descent compiler for the script language -> C against
    the runtime. Statements: assignment (= += -= *= /= %=), if/else, while,
    repeat, exit, blocks, function-call statements, `var` decls. Expressions:
    ints, vars, alarm[i], the usual operators, calls. Identifiers resolve to a
    resource index (object/sprite/room/sound), a built-in/global/key, or a user
    variable slot. Unknown functions/chars raise GmlError, which the generator
    records as a problem against the object and event it came from (see
    _Gen.problems) rather than emitting broken C."""

    FUNCS = {
        "irandom": (1, "rt_random((%s)+1)"),
        "random": (1, "rt_random(%s)"),
        "instance_create": (3, "rt_create(%s, %s, %s)"),
        "instance_destroy": (0, "rt_destroy(self)"),
        "instance_number": (1, "rt_instance_count(%s)"),
        "instance_count": (1, "rt_instance_count(%s)"),
        "place_meeting": (1, "(rt_meeting(self, %s) != 0)"),
        "collision": (1, "(rt_meeting(self, %s) != 0)"),
        "keyboard_check": (1, "rt_key_held(%s)"),
        "keyboard_check_pressed": (1, "rt_key_pressed(%s)"),
        "keyboard_check_released": (1, "rt_key_released(%s)"),
        "sound_play": (1, "rt_play_sound(%s)"),
        "sound_stop": (0, "rt_play_sound(-1)"),
        "room_goto": (1, "rt_room_goto(%s)"),
        "move_toward_point": (3, "rt_move_toward(self, %s, %s, %s)"),
        "abs": (1, "((%s) < 0 ? -(%s) : (%s))"),
        "sign": (1, "((%s) > 0 ? 1 : ((%s) < 0 ? -1 : 0))"),
        "min": (2, "((%s) < (%s) ? (%s) : (%s))"),
        "max": (2, "((%s) > (%s) ? (%s) : (%s))"),
        "draw_text": (3, "rt_draw_text((%s) / 8, (%s) / 8, %s)"),
        "draw_number": (3, "rt_draw_int((%s) / 8, (%s) / 8, %s)"),
        "clear_text": (0, "rt_clear_text()"),
        "game_save": (0, "rt_game_save()"),
        "game_load": (0, "rt_game_load()"),
    }

    def __init__(self, gen, vars_map, globals_map=None):
        self.g = gen
        self.vars = vars_map
        self.globals = globals_map or {}

    # ---- lexer ----
    # Tokens are (kind, value, line) — the 1-based source line is carried so a
    # mistake can be reported where the author wrote it, not just "somewhere in
    # this event". Index tokens by position (t[0]/t[1]), never by unpacking.
    @staticmethod
    def _lex(s):
        toks, i, n = [], 0, len(s or "")
        ln = 1
        two_ops = ("<=", ">=", "==", "!=", "&&", "||", "+=", "-=", "*=", "/=", "%=")
        singles = "+-*/%<>=(){}[],;!."
        while i < n:
            c = s[i]
            if c in " \t\r\n":
                if c == "\n":
                    ln += 1
                i += 1; continue
            if c == "/" and i + 1 < n and s[i + 1] == "/":
                while i < n and s[i] != "\n":
                    i += 1
                continue
            if c.isdigit():
                j = i
                while j < n and s[j].isdigit():
                    j += 1
                toks.append(("num", int(s[i:j]), ln)); i = j; continue
            if c.isalpha() or c == "_":
                j = i
                while j < n and (s[j].isalnum() or s[j] == "_"):
                    j += 1
                toks.append(("id", s[i:j], ln)); i = j; continue
            if c == '"':
                j = i + 1
                buf = []
                esc = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}
                while j < n and s[j] != '"':
                    if s[j] == "\\" and j + 1 < n:
                        buf.append(esc.get(s[j + 1], s[j + 1])); j += 2
                    else:
                        if s[j] == "\n":
                            ln += 1
                        buf.append(s[j]); j += 1
                toks.append(("str", "".join(buf), ln)); i = j + 1; continue
            if s[i:i + 2] in two_ops:
                toks.append(("op", s[i:i + 2], ln)); i += 2; continue
            if c in singles:
                toks.append(("op", c, ln)); i += 1; continue
            raise GmlError(_t("there is a %r here, which means nothing in code") % c,
                           ln)
        return toks

    def _line(self, k=0):
        """The source line of the token `k` ahead, or of the last one seen."""
        t = self._peek(k)
        if t is not None:
            return t[2]
        return self.toks[-1][2] if self.toks else 0

    def _peek(self, k=0):
        p = self.pos + k
        return self.toks[p] if p < len(self.toks) else None

    def _next(self):
        # Running off the end is a real authoring mistake (`x = ` with nothing
        # after it), so report it as one instead of raising IndexError out of
        # the compiler.
        if self.pos >= len(self.toks):
            raise GmlError(_t("the code stops in the middle of something"),
                           self._line())
        t = self.toks[self.pos]; self.pos += 1; return t

    def _eat(self, val):
        t = self._peek()
        if not t or t[1] != val:
            raise GmlError(_t("expected %s here, found %s")
                           % (val, t[1] if t else "the end of the code"),
                           self._line())
        self.pos += 1

    def _is(self, val):
        t = self._peek()
        return bool(t and t[1] == val)

    # ---- entry ----
    def compile(self, code, ind):
        self.toks = self._lex(code)
        self.pos = 0
        out = []
        while self._peek() is not None:
            out += self._stmt(ind)
        return out

    # ---- statements ----
    def _block_or_stmt(self, ind):
        return self._stmt(ind)

    def _stmt(self, ind):
        pad = "    " * ind
        t = self._peek()
        if t is None:
            return []
        if t[1] == ";":
            self._next(); return []
        if t[1] == "{" or t[1] == "begin":
            close = "}" if t[1] == "{" else "end"
            self._next()
            out = []
            while self._peek() and not self._is(close):
                out += self._stmt(ind)
            self._eat(close)
            return out
        # Constructs a C or JavaScript habit reaches for that this language
        # does not have. Without this, `for (i = 0; i < 3; i = i + 1)` was
        # parsed as a call to a function named `for` and reported as "expected
        # ) here, found =" — a complaint about a bracket, when the answer the
        # author needs is which loop to write instead.
        if t[0] == "id" and t[1] in SCRIPT_NOT_HERE:
            raise GmlError(_t("%s: %s") % (t[1], SCRIPT_NOT_HERE[t[1]]),
                           self._line())
        if t[0] == "id" and t[1] == "if":
            self._next(); self._eat("("); cond = self._expr(); self._eat(")")
            if self._is("then"):
                self._next()
            body = self._stmt(ind + 1)
            out = ["%sif (%s) {" % (pad, cond)] + body
            if self._is("else"):
                self._next()
                out.append("%s} else {" % pad)
                out += self._stmt(ind + 1)
            out.append("%s}" % pad)
            return out
        if t[0] == "id" and t[1] == "while":
            self._next(); self._eat("("); cond = self._expr(); self._eat(")")
            return (["%swhile (%s) {" % (pad, cond)] + self._stmt(ind + 1)
                    + ["%s}" % pad])
        if t[0] == "id" and t[1] == "repeat":
            self._next(); self._eat("("); cnt = self._expr(); self._eat(")")
            self.g._loopn += 1
            lv = "_g%d" % self.g._loopn
            return (["%sfor (int %s = (%s); %s > 0; %s--) {" %
                     (pad, lv, cnt, lv, lv)] + self._stmt(ind + 1) + ["%s}" % pad])
        if t[0] == "id" and t[1] == "exit":
            self._next()
            if self._is(";"):
                self._next()
            return ["%sreturn;" % pad]
        if t[0] == "id" and t[1] == "var":
            self._next()
            out = []
            while True:
                name = self._next()[1]
                if self._is("="):
                    self._next()
                    out.append("%s%s = %s;" % (pad, self._var_ref(name),
                                               self._expr()))
                if self._is(","):
                    self._next(); continue
                break
            if self._is(";"):
                self._next()
            return out
        # assignment or call statement
        save = self.pos
        try:
            target = self._lvalue()
            if self._peek() and self._peek()[1] in ("=", "+=", "-=", "*=",
                                                    "/=", "%="):
                op = self._next()[1]
                # Once the `=` is consumed this IS an assignment, so an error
                # in the right-hand side is the real one and must not be
                # swallowed. It used to be: the parser backtracked and
                # re-read the line as a bare expression, and
                # `hspeed = abs(1, 2)` — a plain wrong-argument-count — was
                # reported as "= does not belong here", a complaint about the
                # one part of the line that was correct.
                rhs = self._expr()
                if self._is(";"):
                    self._next()
                return ["%s%s %s %s;" % (pad, target, op, rhs)]
        except GmlError:
            # NOT `self.pos > save and ...`: _lvalue can fail without consuming
            # anything (`1 = 2`), and that short-circuit is what kept its two
            # messages unreachable. _committed decides on its own.
            if self._committed(save):
                raise
        self.pos = save
        expr = self._expr()
        if self._is(";"):
            self._next()
        return ["%s%s;" % (pad, expr)]

    ASSIGN_OPS = ("=", "+=", "-=", "*=", "/=", "%=")

    def _committed(self, save):
        """Is this line an assignment, whatever went wrong in it?

        Two ways to be sure. Past the `=`, the failure is the right-hand
        side's. Before it, look AHEAD: a top-level assignment operator before
        the statement ends means an assignment was intended even though the
        left-hand side never parsed — which is the `1 = 2` and `abs(1) = 2`
        case. Without the look-ahead those two both backtracked and reported
        "= does not belong here", so `this is not something you can assign to`
        and `you cannot assign to the result of f()` could not be reached at
        all.

        Bare calls must still backtrack: `instance_destroy()` is a statement,
        and there is no assignment operator anywhere in it."""
        for k in range(save, min(self.pos, len(self.toks))):
            if self.toks[k][1] in self.ASSIGN_OPS:
                return True
        depth = 0
        for k in range(save, len(self.toks)):
            tok = self.toks[k][1]
            if tok in ("(", "["):
                depth += 1
            elif tok in (")", "]"):
                depth -= 1
            elif depth <= 0:
                if tok in (";", "{", "}"):
                    break
                if tok in self.ASSIGN_OPS:
                    return True
        return False

    def _lvalue(self):
        t = self._peek()
        if not t or t[0] != "id" or t[1] in SCRIPT_KEYWORDS:
            raise GmlError(_t("this is not something you can assign to"),
                           self._line())
        nxt = self._peek(1)
        if nxt and nxt[1] == "(":
            raise GmlError(_t("you cannot assign to the result of %s()") % t[1],
                           self._line())
        name = self._next()[1]
        if self._is("["):
            self._next(); idx = self._expr(); self._eat("]")
            return self._arr_ref(name, idx)
        return self._var_ref(name)

    # ---- expressions (precedence climbing) ----
    def _expr(self):
        return self._binary(0)

    _LEVELS = [("||",), ("&&",), ("==", "!="), ("<", ">", "<=", ">="),
               ("+", "-"), ("*", "/", "%")]

    def _binary(self, lvl):
        if lvl >= len(self._LEVELS):
            return self._unary()
        left = self._binary(lvl + 1)
        while self._peek() and self._peek()[1] in self._LEVELS[lvl]:
            op = self._next()[1]
            right = self._binary(lvl + 1)
            left = "(%s %s %s)" % (left, op, right)
        return left

    def _unary(self):
        t = self._peek()
        if t and t[1] in ("-", "!"):
            self._next()
            return "(%s%s)" % (t[1], self._unary())
        return self._primary()

    def _primary(self):
        t = self._next()
        if t is None:
            raise GmlError(_t("the code stops in the middle of something"),
                           self._line())
        if t[0] == "num":
            return str(t[1])
        if t[0] == "str":
            return _cstr(t[1])
        if t[1] == "(":
            e = self._expr(); self._eat(")")
            return "(%s)" % e
        if t[0] == "id":
            name = t[1]
            if self._is("("):
                return self._call(name)
            if self._is("["):
                self._next(); idx = self._expr(); self._eat("]")
                return self._arr_ref(name, idx)
            return self._var_ref(name)
        raise GmlError(_t("%s does not belong here") % (t[1],), t[2])

    def _call(self, name):
        line = self._line()          # the open bracket, i.e. where the call is
        self._eat("(")
        args = []
        if not self._is(")"):
            args.append(self._expr())
            while self._is(","):
                self._next(); args.append(self._expr())
        self._eat(")")
        spec = self.FUNCS.get(name)
        if spec is None:
            # A function this project's scripts define. Checked after the
            # built-ins so a script cannot shadow one and change what an
            # existing action means.
            argc = getattr(self.g, "script_funcs", {}).get(name)
            if argc is not None:
                if len(args) != argc:
                    # Two whole sentences rather than a "%s" plural slot:
                    # nbi18n hands back the English whenever a translation's
                    # placeholders differ from the source's, and most languages
                    # do not form a plural by adding -s.
                    raise GmlError(
                        (_t("%s takes one value, not %d") % (name, len(args)))
                        if argc == 1 else
                        (_t("%s takes %d values, not %d")
                         % (name, argc, len(args))), line)
                return "%s(%s)" % (name, ", ".join(args))
            raise GmlError(_t("there is no function called %s") % name, line)
        argc, tmpl = spec
        if len(args) != argc:
            if argc == 0:
                msg = _t("%s takes nothing inside its brackets") % name
            elif argc == 1:
                msg = (_t("%s needs one value inside its brackets, not %d")
                       % (name, len(args)))
            else:
                msg = (_t("%s needs %d values inside its brackets, not %d")
                       % (name, argc, len(args)))
            raise GmlError(msg, line)
        # templates repeat some args (min/max/abs/sign); expand by placeholder count
        need = tmpl.count("%s")
        fill = args if need == argc else [args[0]] * need if argc == 1 else (
            [args[0], args[1], args[0], args[1]] if argc == 2 else args)
        return tmpl % tuple(fill)

    # ---- identifier resolution ----
    def _var_ref(self, name):
        if name == "global" and self._is("."):
            self._next()
            field = self._peek()
            if not field or field[0] != "id":
                raise GmlError(_t("global. must be followed by a name"),
                               self._line())
            self._next()
            slot = self.globals.get(field[1])
            if slot is None:
                raise GmlError(_t("global.%s is never set anywhere; set it once "
                               "before you read it") % field[1], self._line())
            return "nb_global[%d]" % slot
        if name in ("true", "false"):
            return "1" if name == "true" else "0"
        if name in self.g.obj_ix:
            return str(self.g.obj_ix[name])
        if name in self.g.spr_ix:
            return str(self.g.spr_ix[name])
        if name in self.g.room_ix:
            return str(self.g.room_ix[name])
        if name in self.g.snd_ix:
            return str(self.g.snd_ix[name])
        if name in SCRIPT_KEYS:
            return SCRIPT_KEYS[name]
        if name in SCRIPT_BUILTIN_VARS:
            return "self->%s" % name
        if name in SCRIPT_GLOBALS:
            return SCRIPT_GLOBALS[name]
        if name in self.vars:
            return "self->var[%d]" % self.vars[name]
        raise GmlError(_t("%s is not a word this code knows; check the spelling")
                       % name, self._line())

    def _arr_ref(self, name, idx):
        if name == "alarm":
            # An instance has MAX_ALARMS of them. `alarm[9] = 30` — written by
            # anyone who assumes there are ten — emitted a write past the end
            # of the array, into the variable slots that follow it in the
            # struct. gcc says "array subscript 9 is above array bounds of
            # 's32[4]'", which names a C type the author has never seen; say
            # it in the language they wrote it in. Only a literal index can be
            # checked: `alarm[i]` is not knowable here.
            # A bare literal arrives as "3"; anything else is parenthesised,
            # so "-1" reaches here as "(-1)". Peel those off before deciding
            # whether this is a number at all — an expression like (1 + 2)
            # cannot be judged here and is left to the compiler.
            lit = str(idx).strip()
            while len(lit) > 1 and lit[0] == "(" and lit[-1] == ")":
                lit = lit[1:-1].strip()
            if re.fullmatch(r"-?\d+", lit) and not (0 <= int(lit) < MAX_ALARMS):
                raise GmlError(
                    _t("there is no alarm %s; an object has %d, numbered 0 to %d")
                    % (lit, MAX_ALARMS, MAX_ALARMS - 1), self._line())
            return "self->alarm[%s]" % idx
        raise GmlError(_t("unknown array %s") % name)


# ---------------------------------------------------------------- codegen
# Words C will not accept as a name. A table column called "char" — which is
# what a character table's first column gets called — emitted `char char;` and
# the build died with "two or more data types in declaration", naming a line of
# generated code the author has never seen. Trailing underscore instead.
C_KEYWORDS = frozenset("""
auto break case char const continue default do double else enum extern float
for goto if inline int long register restrict return short signed sizeof static
struct switch typedef union unsigned void volatile while
_Bool _Complex _Imaginary _Alignas _Alignof _Atomic _Generic _Noreturn
_Static_assert _Thread_local
""".split())


# Every nb_* identifier the runtime already owns. A table's C name is minted
# from what the author typed, prefixed nb_ — so a table called "score" emitted
# `nb_score`, which runtime.h declares as `extern s32`, and the build died with
# "conflicting types for 'nb_score'" pointing into a header the author has
# never opened. "Score", "Rooms", "Objects" and "Health" are exactly the names
# a first game's tables get.
#
# Over-approximating is deliberate and free: reserving a name the runtime only
# mentions costs one underscore on a generated identifier. The list is checked
# against the runtime by tools/gbaruntime_selftest.py, so it cannot drift.
RESERVED_C = frozenset("""
nb_DateTime nb_Fx nb_InstanceDef nb_Object nb_Room nb_Sound nb_Sprite nb_Warp
nb_atan_q nb_bg_palette nb_bg_tile_count nb_bg_tiles nb_event_fn nb_font
nb_font_w nb_fx nb_fx_prio nb_global nb_health nb_lives nb_note_freq
nb_obj_palette nb_obj_tile_count nb_obj_tiles nb_object_count nb_objects
nb_room_count nb_rooms nb_score nb_sin_q nb_sound_count nb_sounds
nb_aff_tiles nb_aff_tile_count nb_aff_palette
nb_sprite_count nb_sprites nb_sram_sig nb_save_type nb_save_sig nb_start_room nb_text_bank
""".split())


class _Gen:
    def __init__(self, model):
        self.m = model if isinstance(model, dict) else {}
        self.sprites = self.m.get("sprites") or []
        self.objects = self.m.get("objects") or []
        self.rooms = self.m.get("rooms") or []
        self.sounds = self.m.get("sounds") or []
        self.tilesets = self.m.get("tilesets") or []
        # id -> index maps
        self.spr_ix = {s.get("id"): i for i, s in enumerate(self.sprites)}
        self.obj_ix = {o.get("id"): i for i, o in enumerate(self.objects)}
        self.room_ix = {r.get("id"): i for i, r in enumerate(self.rooms)}
        self.snd_ix = {s.get("id"): i for i, s in enumerate(self.sounds)}
        self.out = []
        self._loopn = 0     # unique counter for generated repeat-loop variables
        # Mistakes found in the model while generating. A bad line of script used
        # to become a silent C comment: the ROM built, the code did nothing, and
        # the author was never told. Collect them here so the IDE can show them.
        self.problems = []
        self._where = ""    # "object · event" being emitted, for problem text
        self._obj_id = ""
        self._fns = {}      # id(object dict) -> (cid, has_create, has_step, has_destroy)
        self.global_ix = self._collect_globals()   # global.* name -> slot
        # Functions the project's own scripts define, callable from any action.
        self.script_funcs = self._collect_script_funcs()
        self.menus = []          # (array name, lines) for Show Menu actions
        self._menu_n = 0
        # Seeded with the runtime's own globals, not empty: the generator
        # must not mint a name the runtime already defines.
        self._cnames = set(RESERVED_C)
        self.audit_vars = True   # off while previewing a single event
        self._banks_wanted = 0   # sprites that needed a colour set and got none

    def _unique_c(self, name, forms=("%s",)):
        """`name`, or the next free variant of it.

        Tables and menus both mint names from author text and neither can see
        the other's. Colliding is a compile error in generated code; renaming
        is a table whose C name has an underscore on the end.

        `forms` are the actual SYMBOLS this name becomes — a table called
        "score" emits `nb_row_score`, `nb_score` and `nb_score_count`, and it
        is those, not the bare ident, that have to be free. Checking the ident
        alone is why `RESERVED_C` did nothing on the first attempt: nothing
        collides with `score`, and everything collided with `nb_score`."""
        base = name
        n = 2
        while any((f % name) in self._cnames for f in forms):
            name = "%s_%d" % (base, n)
            n += 1
        for f in forms:
            self._cnames.add(f % name)
        return name

    def w(self, line=""):
        self.out.append(line)

    # ---- sprites (mode 0: 4bpp hardware OBJ tiles + per-sprite palette banks) --
    OBJ_DIMS = {
        (8, 8): (0, 0), (16, 16): (0, 1), (32, 32): (0, 2), (64, 64): (0, 3),
        (16, 8): (1, 0), (32, 8): (1, 1), (32, 16): (1, 2), (64, 32): (1, 3),
        (8, 16): (2, 0), (8, 32): (2, 1), (16, 32): (2, 2), (32, 64): (2, 3),
    }

    @classmethod
    def _obj_size(cls, w, h):
        """(vw, vh, shape, size) for a valid GBA OBJ; snaps each dimension to a
        legal size (square/wide/tall), falling back to a square that fits."""
        def snap(v):
            for c in (8, 16, 32, 64):
                if c >= int(v):
                    return c
            return 64
        vw, vh = snap(w), snap(h)
        if (vw, vh) in cls.OBJ_DIMS:
            sh, sz = cls.OBJ_DIMS[(vw, vh)]
            return vw, vh, sh, sz
        n = max(vw, vh)
        sh, sz = cls.OBJ_DIMS[(n, n)]
        return n, n, sh, sz

    def _build_obj_palette(self):
        """Give each sprite its OWN 16-colour hardware palette bank (index 0 =
        transparent), so a game shows far more than 16 colours at once. A sprite
        reuses an existing bank whose colours it fits, else gets a new bank (up to
        16). Sets self._spr_bank / self._spr_cmap; returns the 256-entry palette."""
        banks = []          # each: {colour: index 1..15}
        self._spr_bank = {}
        self._spr_cmap = {}
        for si, s in enumerate(self.sprites):
            cols = []
            for fr in s.get("frames") or []:
                for p in fr:
                    c = _int(p, TRANSPARENT) & 0x7FFF
                    if c != (TRANSPARENT & 0x7FFF) and c not in cols:
                        cols.append(c)
            # The hardware limit, said out loud. A sprite gets fifteen colours
            # plus transparent; the sixteenth onwards used to be quietly mapped
            # to index 0, which is TRANSPARENT -- so the extra colours came out
            # of the compiler as holes in the picture and nothing said so.
            if len(cols) > 15:
                self.problems.append(
                    "%s - it is painted in %d colours, and a Game Boy Advance "
                    "sprite can hold 15. The %d after the first 15 will come "
                    "out as holes."
                    % (s.get("name") or s.get("id") or "?", len(cols),
                       len(cols) - 15))
            cols = cols[:15]
            chosen = None
            # An author may pin a sprite to a bank. Two sprites sharing a bank
            # can share tiles and cost less VRAM, and pinning is the only way to
            # say which two -- the allocator packs in sprite order and has no
            # way to know that these two are the same character.
            pin = s.get("pal_bank")
            if isinstance(pin, int) and 0 <= pin < 16:
                while len(banks) <= pin:
                    banks.append({})
                bank = banks[pin]
                missing = [c for c in cols if c not in bank]
                if len(bank) + len(missing) <= 15:
                    for c in missing:
                        bank[c] = len(bank) + 1
                    chosen = pin
                else:
                    self.problems.append(
                        "%s is pinned to colour set %d, which has room for %d "
                        "more colours and needs %d. It has been placed "
                        "elsewhere." % (s.get("name") or s.get("id") or "?",
                                        pin, 15 - len(bank), len(missing)))
            for bi, bank in enumerate(banks):
                if chosen is not None:
                    break
                missing = [c for c in cols if c not in bank]
                if len(bank) + len(missing) <= 15:
                    for c in missing:
                        bank[c] = len(bank) + 1
                    chosen = bi
                    break
            if chosen is None and len(banks) < 16:
                bank = {}
                for c in cols:
                    bank[c] = len(bank) + 1
                banks.append(bank)
                chosen = len(banks) - 1
            if chosen is None:
                chosen = 0          # 16 banks exhausted: fold onto bank 0
                # Count it. Otherwise the costing pane reports "16 / 16" and
                # calls the project fine, while check_project says four sprites
                # will be drawn in someone else's colours — two diagnostics
                # disagreeing about the same fact, and the reassuring one is
                # the one an author is more likely to look at.
                self._banks_wanted += 1
                self.problems.append(
                    "%s - the game has run out of sprite colour sets (there are "
                    "16), so this sprite will be drawn in another sprite's "
                    "colours." % (s.get("name") or s.get("id") or "?"))
            self._spr_bank[si] = chosen
            cmap = {TRANSPARENT & 0x7FFF: 0}
            for c in cols:
                cmap[c] = banks[chosen].get(c, 1)
            self._spr_cmap[si] = cmap
        pal = [0] * 256
        for bi, bank in enumerate(banks):
            for c, idx in bank.items():
                pal[bi * 16 + idx] = c
        self._banks = banks
        return pal

    def _obj_tiles(self, s, si):
        """(vw, vh, u16 tile stream) for all frames of sprite index `si`, in GBA
        4bpp OBJ format: 1D-mapped tiles, pixels row-major, low nibble = leftmost,
        4 px per u16."""
        vw, vh, _sh, _sz = self._obj_size(_int(s.get("w"), 16), _int(s.get("h"), 16))
        tw, th = vw // 8, vh // 8
        cmap = self._spr_cmap.get(si, {TRANSPARENT & 0x7FFF: 0})
        out = []
        for fr in s.get("frames") or [[]]:
            px = list(fr) if isinstance(fr, (list, tuple)) else []
            px = (px + [TRANSPARENT] * (vw * vh))[:vw * vh]
            idx = [cmap.get(_int(p, TRANSPARENT) & 0x7FFF, 0) for p in px]
            for ty in range(th):
                for tx in range(tw):
                    for row in range(8):
                        base = (ty * 8 + row) * vw + tx * 8
                        for half in range(2):
                            v = 0
                            for k in range(4):
                                v |= (idx[base + half * 4 + k] & 0xF) << (k * 4)
                            out.append(v)
        return vw, vh, out

    def gen_sprites(self):
        pal = self._build_obj_palette()
        self.w("const u16 nb_obj_palette[256] = { %s };"
               % ", ".join("0x%04X" % (c & 0x7FFF) for c in pal))
        self.w("")
        meta = []            # (base, tpf, vw, vh, shape, size, palbank)
        all_tiles = []
        for si, s in enumerate(self.sprites):
            vw, vh, tiles = self._obj_tiles(s, si)
            _, _, shape, size = self._obj_size(_int(s.get("w"), 16),
                                               _int(s.get("h"), 16))
            tpf = (vw // 8) * (vh // 8)
            base = len(all_tiles) // 16      # 16 u16 = one 32-byte 4bpp tile
            meta.append((base, tpf, vw, vh, shape, size, self._spr_bank.get(si, 0)))
            all_tiles.extend(tiles)
        body = ", ".join("0x%04X" % v for v in all_tiles) or "0x0000"
        self.w("const u16 nb_obj_tiles[] = { %s };" % body)
        self.w("const int nb_obj_tile_count = %d;" % (len(all_tiles) // 16))
        self.w("")
        self.w("const nb_Sprite nb_sprites[] = {")
        for i, s in enumerate(self.sprites):
            base, tpf, vw, vh, shape, size, palbank = meta[i]
            ox = _int(s.get("ox"), vw // 2)
            oy = _int(s.get("oy"), vh // 2)
            nf = max(1, len(s.get("frames") or [[]]))
            anim = max(0, min(255, _int(s.get("anim_speed"), 0)))
            self.w("    { %d, %d, %d, %d, %d, %d, %d, %d, %d, %d, %d }," %
                   (vw, vh, ox, oy, nf, base, tpf, shape, size, palbank, anim))
        if not self.sprites:
            self.w("    { 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0 },")
        self.w("};")
        self.w("const int nb_sprite_count = %d;" % len(self.sprites))
        self.w("")

    # ---- backgrounds / tileset (mode 0: 4bpp BG tiles + a shared palette) ----
    def gen_bg(self):
        """Shared BG palette + tileset tiles. Charblock tile 0 is a blank tile so
        a tilemap entry of 0 = empty (shows the room backdrop); the tileset's
        tiles follow at charblock indices 1..N, which is what a room tilemap value
        indexes directly. Index 0 within a tile is transparent."""
        cmap = {TRANSPARENT & 0x7FFF: 0}
        pal = [0] * 16
        nxt = 1
        over = set()                        # colours that did not fit the 15
        tiles = [0] * 16                    # tile 0 = blank
        # Which 8x8 charblock cells each AUTHORED tile became. A 16x16 tile is
        # four cells and a 32x32 is sixteen, and the room tilemap addresses
        # cells -- so marking a tile solid has to mark every cell it occupies.
        self._cell_of = {}                  # (tileset index, tile index) -> [cells]
        for tsi, ts in enumerate(self.tilesets):
            # A tile set is authored at 8, 16 or 32 px. The hardware only has
            # 8x8 BG tiles, so a bigger tile becomes (size/8)^2 of them in
            # BLOCK ROW-MAJOR order -- the same order gbasdk.split_tile uses
            # and the order a room tilemap was painted in. Get this order wrong
            # and every large tile shows scrambled on the console while looking
            # perfect in the editor.
            tsz = _int(ts.get("size", 8), 8)
            if tsz not in (8, 16, 32):
                tsz = 8
            blocks = max(1, tsz // 8)
            for tli, tile in enumerate(ts.get("tiles") or []):
                first_cell = len(tiles) // 16
                whole = list(tile or [])
                need = tsz * tsz
                whole = (whole + [TRANSPARENT] * need)[:need]
                cells = []
                for by in range(blocks):
                    for bx in range(blocks):
                        cell = []
                        for j in range(8):
                            row = (by * 8 + j) * tsz + bx * 8
                            cell.extend(whole[row:row + 8])
                        cells.append(cell)
                for px in cells:
                    idx = []
                    for p in px:
                        c = _int(p, TRANSPARENT) & 0x7FFF
                        if c not in cmap:
                            if nxt < 16:
                                cmap[c] = nxt
                                pal[nxt] = c
                                nxt += 1
                            else:
                                # All the tile sets share ONE 15-colour background
                                # palette. Over that, a colour was silently swapped
                                # for whatever happens to be in slot 1.
                                cmap[c] = 1
                                over.add(c)
                        idx.append(cmap[c])
                    for row in range(8):
                        for half in range(2):
                            v = 0
                            for k in range(4):
                                v |= (idx[row * 8 + half * 4 + k] & 0xF) << (k * 4)
                            tiles.append(v)
                self._cell_of[(tsi, tli)] = list(
                    range(first_cell, len(tiles) // 16))
        self._bg_cmap = cmap
        # A 4bpp charblock holds 512 8x8 tiles, and index 0 is the blank one.
        # At 8x8 nobody reaches that; at 32x32 each tile costs SIXTEEN slots, so
        # 32 tiles fill the block. Say which, because "too many tiles" is
        # baffling when the set only has 32 pictures in it.
        used = len(tiles) // 16                 # 16 u16 words per 8x8 tile
        if used > 512:
            self.problems.append(
                "The tile sets need %d background tiles and the Game Boy "
                "Advance holds 512. A 16 x 16 tile counts as 4 and a 32 x 32 "
                "tile counts as 16. Use fewer tiles, or a smaller tile size."
                % used)
        if over:
            self.problems.append(
                "The tiles use %d colours between them, and all the tile sets "
                "in a game share one set of 15. The %d that did not fit will be "
                "drawn in the wrong colour."
                % (len(cmap) - 1 + len(over), len(over)))
        self.w("const u16 nb_bg_palette[16] = { %s };"
               % ", ".join("0x%04X" % (c & 0x7FFF) for c in pal))
        self.w("const u16 nb_bg_tiles[] = { %s };"
               % (", ".join("0x%04X" % v for v in tiles) or "0x0000"))
        self.w("const int nb_bg_tile_count = %d;" % (len(tiles) // 16))
        self._emit_affine_tiles()
        self.w("")
        self._emit_tile_solid(len(tiles) // 16)

    def _emit_tile_solid(self, ncells):
        """One byte per charblock cell: nonzero means a wall or a floor.

        The runtime has read this table since it was written, and the generator
        never emitted it -- so `g_has_solid` stayed 0, every tile test returned
        "free", and TILE COLLISION DID NOT WORK IN ANY BUILT GAME. A tile floor
        stopped nothing and rt_on_ground could only ever see a solid object.
        Nothing reported it, because a table of zeroes is a valid table.

        Solidity is authored per TILE and consumed per CELL, so a solid 16x16
        tile marks all four of its cells."""
        solid = [0] * max(1, ncells)
        any_solid = False
        for tsi, ts in enumerate(self.tilesets):
            flags = ts.get("solid") or []
            for tli in range(len(ts.get("tiles") or [])):
                if tli >= len(flags) or not flags[tli]:
                    continue
                for cell in self._cell_of.get((tsi, tli), ()):
                    if 0 <= cell < len(solid):
                        solid[cell] = 1
                        any_solid = True
        self._has_solid = any_solid
        self.w("const u8 nb_tile_solid[] = { %s };"
               % ", ".join(str(v) for v in solid))
        self.w("")

    # ---- objects / events / actions ----
    def _collect_vars(self, obj):
        """Ordered unique user-variable names referenced in an object."""
        names = []
        # A variable this object only ever READS can never be anything but
        # zero. The language gives a slot to any identifier it does not
        # recognise — that is what makes `wobble = 7` work, and it is also why
        # `hspeed` misspelt as `hspee` compiles to a variable nothing looks at
        # and the object simply does not move.
        assigned, readonly, has_c = set(), set(), [False]
        say_slots_ahead = []

        def scan(actions):
            for a in actions or []:
                if not isinstance(a, dict):
                    continue
                k = a.get("kind")
                if k in ("set_var", "add_var", "if_var", "menu"):
                    v = str(a.get("var", "")).strip()
                    if v and v not in names:
                        names.append(v)
                    if v:
                        # Show Menu writes the chosen row into its variable
                        # from the ENGINE, so it counts as setting it even
                        # though no script line does.
                        (readonly if k == "if_var" else assigned).add(v)
                elif k == "say":
                    # {v:N} in dialogue prints variable SLOT N: it is a read,
                    # made by the engine while typing the line. Without this,
                    # a variable whose only purpose is being shown to the
                    # player — a gold count in a shop line — was reported as
                    # "set but never read", and the fix the message suggests
                    # would break the dialogue that displays it. Slots are
                    # handed out in `names` order, so slot N is names[N].
                    for m in re.finditer(r"\{v:(\d+)\}", str(a.get("text", ""))):
                        slot = int(m.group(1))
                        if slot < len(names):
                            readonly.add(names[slot])
                        else:
                            say_slots_ahead.append(slot)
                elif k == "execute_code":
                    # A C block declares its own variables in C. Scanning it for
                    # script names would allocate instance slots for locals and
                    # could push a real variable past the twelve there are.
                    if str(a.get("lang") or "").strip().lower() != "c":
                        for v in self._gml_user_vars(a.get("code", "")):
                            if v not in names:
                                names.append(v)
                        _a, _r = self._gml_var_uses(a.get("code", ""))
                        assigned.update(_a)
                        readonly.update(_r)
                    else:
                        # A C block can write self->var[] directly, so nothing
                        # in this object can be called never-set after one.
                        has_c[0] = True
                scan(a.get("children"))

        for ev in obj.get("events") or []:
            scan(ev.get("actions"))
        # A say that printed a slot before the walk had reached the action
        # declaring its variable could not name it yet; the slot order is
        # final now, so resolve those reads.
        for slot in say_slots_ahead:
            if slot < len(names):
                readonly.add(names[slot])
        # An instance carries 12 variable slots. Past that the name has no slot,
        # and every action using it used to emit NOTHING — no code, no message,
        # a ROM that built clean and quietly did less than it was told. Say so.
        if len(names) > 12:
            self._problem(_t("%s uses %d variables; an instance holds 12. "
                          "Dropped: %s") % (obj.get("name") or obj.get("id") or "Object",
                                           len(names), ", ".join(names[12:])))
        # Not while PREVIEWING. "Show C" is the teaching device: it shows one
        # event, often one the author is still writing, and a variable set in a
        # sibling event they have not reached yet is not a mistake. The audit
        # belongs to the pre-export gate, which sees the finished project.
        if not has_c[0] and self.audit_vars:
            who = obj.get("name") or obj.get("id") or "Object"
            for v in names:
                near = self._did_you_mean(v)
                hint = (" Did you mean %s?" % near) if near else ""
                if v not in assigned and v in readonly:
                    self._problem(
                        _t("%s reads %s but never sets it, so it is always 0.%s")
                        % (who, v, hint))
                elif v in assigned and v not in readonly:
                    # A slot lives on the instance and nothing outside the
                    # object can read it, so setting one and never reading it
                    # does nothing at all. This is what a misspelt built-in
                    # looks like: `hspee = 2` compiles, and the object sits
                    # still.
                    self._problem(
                        _t("%s sets %s but never reads it, so it has no effect.%s")
                        % (who, v, hint))
        return {n: i for i, n in enumerate(names[:12])}

    @staticmethod
    def _did_you_mean(name):
        """The built-in this name is one edit away from, if any.

        Deliberately distance ONE. Two edits reaches far enough to suggest
        `grav` for `drag`, which is a different idea and a worse guess than
        saying nothing."""
        if len(name) < 3:
            # One edit away from a two-letter name is half the alphabet;
            # "did you mean x?" for a variable called c is not a suggestion.
            return None
        best = None
        for cand in sorted(set(SCRIPT_BUILTIN_VARS) | set(SCRIPT_GLOBALS)):
            if abs(len(cand) - len(name)) > 1 or cand == name:
                continue
            # one substitution, insertion or deletion
            if len(cand) == len(name):
                if sum(1 for a, b in zip(cand, name) if a != b) == 1:
                    best = cand
            else:
                lo, hi = (name, cand) if len(cand) > len(name) else (cand, name)
                for i in range(len(hi)):
                    if hi[:i] + hi[i + 1:] == lo:
                        best = cand
                        break
            if best:
                return best
        return None

    # A C function definition at file scope: return type, name, parameters,
    # then an opening brace. Approximate on purpose -- the compiler is the
    # authority on whether a script is valid C; this only has to know which
    # NAMES a project offers so that calling one from an action is not
    # rejected as unknown.
    _FUNC_DEF = re.compile(
        r"^[A-Za-z_][A-Za-z0-9_ \t]*[ \t*]+([A-Za-z_][A-Za-z0-9_]*)"
        r"[ \t]*\(([^;{)]*)\)[ \t]*\n?[ \t]*\{", re.M)

    def _collect_script_funcs(self):
        """name -> argument count, for every function the project's scripts
        define.

        Without this a script was unreachable: the action-code compiler
        rejected every call it did not already know, so an Execute Code action
        calling a script function had its WHOLE block replaced by a comment.
        The ROM still built. That is the failure this project has been bitten
        by repeatedly -- work quietly not done, reported as a problem nobody
        had reason to read."""
        out = {}
        for sc in self.m.get("scripts") or []:
            if not isinstance(sc, dict):
                continue
            code = sc.get("code")
            if not isinstance(code, str):
                continue
            # Strip comments and string literals first, or a function-looking
            # line inside either is offered as a real name.
            clean = re.sub(r"/\*.*?\*/", " ", code, flags=re.S)
            clean = re.sub(r"//[^\n]*", " ", clean)
            clean = re.sub(r'"(?:[^"\\]|\\.)*"', '""', clean)
            for m in self._FUNC_DEF.finditer(clean):
                name, params = m.group(1), m.group(2).strip()
                if name in ("if", "while", "for", "switch", "return", "do"):
                    continue
                if not params or params == "void":
                    argc = 0
                else:
                    argc = len([x for x in params.split(",") if x.strip()])
                out[name] = argc
        return out

    def _collect_globals(self):
        """Project-wide global.* names -> slot (persistent, saved to SRAM)."""
        names = []

        def scan(acts):
            for a in acts or []:
                if not isinstance(a, dict):
                    continue
                if a.get("kind") == "execute_code":
                    if str(a.get("lang") or "").strip().lower() != "c":
                        for nm in self._gml_globals(a.get("code", "")):
                            if nm not in names:
                                names.append(nm)
                        assigned.update(
                            self._gml_globals_set(a.get("code", "")))
                scan(a.get("children"))

        assigned = set()
        for o in self.objects:
            for ev in o.get("events") or []:
                scan(ev.get("actions"))
        # A global read but assigned NOWHERE in the project is always zero.
        # The message for it already existed and could never fire: every
        # `global.x` mention won a slot, read or write alike, so the "is it
        # known?" test it hung on always passed. Same shape as the instance
        # variables, one scope up.
        for nm in names:
            if nm not in assigned:
                self._problem(_t("global.%s is never set anywhere; set it once "
                                 "before you read it") % nm)
        if len(names) > 32:
            self._problem(_t("Project uses %d globals; 32 are saved to the "
                          "cartridge. Dropped: %s")
                          % (len(names), ", ".join(names[32:])))
        return {n: i for i, n in enumerate(names[:32])}

    @staticmethod
    def _gml_globals_set(code):
        """The `global.NAME`s this script ASSIGNS to, as opposed to mentions."""
        try:
            toks = _Gml._lex(code)
        except GmlError:
            return set()
        out = set()
        for i in range(len(toks) - 3):
            if (toks[i][0] == "id" and toks[i][1] == "global"
                    and toks[i + 1][1] == "." and toks[i + 2][0] == "id"
                    and toks[i + 3][1] in ("=", "+=", "-=", "*=", "/=", "%=")):
                out.add(toks[i + 2][1])
        return out

    @staticmethod
    def _gml_globals(code):
        try:
            toks = _Gml._lex(code)
        except GmlError:
            return []
        out = []
        for i in range(len(toks) - 2):
            if (toks[i][0] == "id" and toks[i][1] == "global"
                    and toks[i + 1][1] == "." and toks[i + 2][0] == "id"):
                nm = toks[i + 2][1]
                if nm not in out:
                    out.append(nm)
        return out

    @staticmethod
    def _gml_var_uses(code):
        """(assigned, read) identifier names in one script.

        An identifier immediately followed by an assignment operator is being
        SET; every other mention is a READ. Needed because a name that is only
        ever read can never be anything but zero, and the language cannot tell
        that from the name alone: it gives a slot to any identifier it does not
        recognise, which is what makes `wobble = 7` work — and what makes
        `hspee = 2` compile to a variable nothing looks at."""
        try:
            toks = _Gml._lex(code)
        except GmlError:
            return set(), set()
        ASSIGN = ("=", "+=", "-=", "*=", "/=")
        assigned, read = set(), set()
        for i, t in enumerate(toks):
            if t[0] != "id":
                continue
            if t[1] == "global":
                continue                       # a namespace, not a variable
            if i and toks[i - 1][1] == ".":
                continue                       # global.NAME, not a variable
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            if nxt and nxt[1] == "(":
                continue                       # a call, not a variable
            if nxt and nxt[1] == "[":
                # `alarm[0] = 30` sets alarm. Walk to the matching bracket and
                # look at what follows, or an indexed write reads as a read.
                depth, j = 0, i + 1
                while j < len(toks):
                    if toks[j][1] == "[":
                        depth += 1
                    elif toks[j][1] == "]":
                        depth -= 1
                        if depth == 0:
                            break
                    j += 1
                after = toks[j + 1] if j + 1 < len(toks) else None
                if after and after[1] in ASSIGN and after[1] != "==":
                    assigned.add(t[1])
                else:
                    read.add(t[1])
            elif nxt and nxt[1] in ASSIGN and nxt[1] != "==":
                assigned.add(t[1])
            else:
                read.add(t[1])
        return assigned, read

    def _gml_user_vars(self, code):
        """Identifiers in the script that are user variables — not resources, built-ins,
        globals, keys, keywords or function calls — so they get var[] slots."""
        try:
            toks = _Gml._lex(code)
        except GmlError:
            return []
        out = []
        for i, t in enumerate(toks):
            if t[0] != "id":
                continue
            name = t[1]
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            if nxt and nxt[1] == "(":
                continue
            if (name in SCRIPT_KEYWORDS or name in SCRIPT_BUILTIN_VARS
                    or name in SCRIPT_GLOBALS or name in SCRIPT_KEYS
                    or name == "alarm"      # a built-in ARRAY, not a slot
                    # A word the language does not have is not a variable
                    # either: `function foo() { }` earned a slot for
                    # "function" and then a note that it is never set, ahead
                    # of the message that actually explains the mistake.
                    or name in SCRIPT_NOT_HERE
                    # `global.x` is a namespace, not a variable called
                    # "global"; `foo[0]` is an array reference, reported as
                    # an unknown array. Both were earning instance slots and
                    # then a note that they are never set — noise printed
                    # AHEAD of the message that explains the real mistake.
                    or name == "global"
                    or (i and toks[i - 1][1] == ".")   # global.NAME
                    or (nxt and nxt[1] == "[")
                    or name in self.obj_ix or name in self.spr_ix
                    or name in self.room_ix or name in self.snd_ix):
                continue
            if name not in out:
                out.append(name)
        return out

    def _val(self, v, vars_map):
        """A model value -> a C integer expression. Bare var names resolve to
        self->var[]; otherwise an integer literal (0 if unparseable)."""
        s = str(v).strip() if v is not None else ""
        if s in vars_map:
            return "self->var[%d]" % vars_map[s]
        if s in ("x", "y", "hspeed", "vspeed", "image_index"):
            return "self->%s" % s
        # score / lives / health, the three values the action palette can Set,
        # Add to and test. Without this they parsed as the number zero, so a
        # score read-out built entirely from actions -- Draw Number, value
        # "score" -- drew a permanent 0 and the only way to show a score was to
        # drop into code. A user variable of the same name still wins above.
        if s in SCRIPT_GLOBALS:
            return SCRIPT_GLOBALS[s]
        return str(_int(s, 0))

    def _problem(self, text):
        """A limit reached or a rule broken, stated plainly and once."""
        if text not in self.problems:
            self.problems.append(text)

    def _missing(self, action_label, kind_word, name):
        """Report an action that points at a resource which is not in the
        project. Every one of these used to be dropped in silence: the ROM built,
        that row of the sheet did nothing, and nobody was told -- which is what
        happens to every reference to a resource the author deletes."""
        where = self._where or (self._obj_id or "?")
        self.problems.append(
            "%s - %s names a %s called \u201c%s\u201d, and there is no such %s "
            "any more, so that line will do nothing."
            % (where, action_label, kind_word, name if name else "?", kind_word))

    def _emit_action(self, a, vars_map, ind):
        pad = "    " * ind
        L = []
        if not isinstance(a, dict):
            return L
        k = a.get("kind")
        if k == "move_fixed":
            hx, vy = DIR_SPEED.get(str(a.get("dir", "stop")), (0, 0))
            sp = _int(a.get("speed"), 2)
            L.append("%sself->hspeed = %d;" % (pad, hx * sp))
            L.append("%sself->vspeed = %d;" % (pad, vy * sp))
        elif k == "set_hspeed":
            L.append("%sself->hspeed = %s;" % (pad, self._val(a.get("value"), vars_map)))
        elif k == "set_vspeed":
            L.append("%sself->vspeed = %s;" % (pad, self._val(a.get("value"), vars_map)))
        elif k == "jump_to":
            L.append("%sself->x = %s;" % (pad, self._val(a.get("x"), vars_map)))
            L.append("%sself->y = %s;" % (pad, self._val(a.get("y"), vars_map)))
        elif k == "jump_relative":
            L.append("%sself->x += %s;" % (pad, self._val(a.get("x"), vars_map)))
            L.append("%sself->y += %s;" % (pad, self._val(a.get("y"), vars_map)))
        elif k == "wrap":
            L.append("%sif (self->x < 0) self->x += %d;" % (pad, 240))
            L.append("%sif (self->x >= %d) self->x -= %d;" % (pad, 240, 240))
            L.append("%sif (self->y < 0) self->y += %d;" % (pad, 160))
            L.append("%sif (self->y >= %d) self->y -= %d;" % (pad, 160, 160))
        elif k == "destroy_self":
            L.append("%srt_destroy(self);" % pad)
        elif k == "create_instance":
            oi = self.obj_ix.get(a.get("object"))
            if oi is not None:
                L.append("%srt_create(%d, %s, %s);" %
                         (pad, oi, self._val(a.get("x"), vars_map),
                          self._val(a.get("y"), vars_map)))
            else:
                self._missing("Create Instance", "object", a.get("object") or a.get("_was"))
        elif k == "set_var":
            idx = vars_map.get(str(a.get("var", "")).strip())
            if idx is not None:
                L.append("%sself->var[%d] = %s;" %
                         (pad, idx, self._val(a.get("value"), vars_map)))
        elif k == "add_var":
            idx = vars_map.get(str(a.get("var", "")).strip())
            if idx is not None:
                L.append("%sself->var[%d] += %s;" %
                         (pad, idx, self._val(a.get("value"), vars_map)))
        elif k == "goto_room":
            ri = self.room_ix.get(a.get("room"))
            if ri is not None:
                L.append("%srt_room_goto(%d);" % (pad, ri))
            else:
                self._missing("Go To Room", "room", a.get("room") or a.get("_was"))
        elif k == "play_sound":
            macro = SFX_MACRO.get(str(a.get("sound") or ""))
            if macro:
                L.append("%srt_sfx(%s);" % (pad, macro))
                return L
            si = self.snd_ix.get(a.get("sound"))
            if si is not None:
                L.append("%srt_play_sound(%d);" % (pad, si))
            else:
                self._missing("Play Sound", "sound", a.get("sound") or a.get("_was"))
        elif k == "stop_sound":
            L.append("%srt_play_sound(-1);" % pad)
        elif k in ("if_key", "if_pressed"):
            mac = KEY_MACRO.get(str(a.get("key", "")).lower())
            if mac:
                fn = "rt_key_held" if k == "if_key" else "rt_key_pressed"
                L.append("%sif (%s(%s)) {" % (pad, fn, mac))
                for c in a.get("children") or []:
                    L += self._emit_action(c, vars_map, ind + 1)
                L.append("%s}" % pad)
        elif k == "if_collision":
            oi = self.obj_ix.get(a.get("object"))
            if oi is None:
                self._missing("If Collision", "object", a.get("object") or a.get("_was"))
            if oi is not None:
                L.append("%sif (rt_meeting(self, %d)) {" % (pad, oi))
                for c in a.get("children") or []:
                    L += self._emit_action(c, vars_map, ind + 1)
                L.append("%s}" % pad)
        elif k == "if_var":
            idx = vars_map.get(str(a.get("var", "")).strip())
            if idx is not None:
                op = CMP_OP.get(str(a.get("op", "==")), "==")
                L.append("%sif (self->var[%d] %s %s) {" %
                         (pad, idx, op, self._val(a.get("value"), vars_map)))
                for c in a.get("children") or []:
                    L += self._emit_action(c, vars_map, ind + 1)
                L.append("%s}" % pad)
        elif k == "set_alarm":
            n = _int(a.get("alarm"), 0)
            if 0 <= n < 4:
                L.append("%sself->alarm[%d] = %s;" %
                         (pad, n, self._val(a.get("steps"), vars_map)))
        elif k == "if_chance":
            L.append("%sif (rt_random(100) < %d) {" % (pad, _int(a.get("percent"), 50)))
            for c in a.get("children") or []:
                L += self._emit_action(c, vars_map, ind + 1)
            L.append("%s}" % pad)
        elif k == "repeat":
            self._loopn += 1
            lv = "_r%d" % self._loopn
            L.append("%sfor (int %s = 0; %s < %d; %s++) {" %
                     (pad, lv, lv, max(0, _int(a.get("count"), 1)), lv))
            for c in a.get("children") or []:
                L += self._emit_action(c, vars_map, ind + 1)
            L.append("%s}" % pad)
        elif k == "exit_event":
            L.append("%sreturn;" % pad)
        elif k == "set_gravity":
            L.append("%sself->grav = %s;" % (pad, self._val(a.get("value"), vars_map)))
        elif k == "move_toward":
            L.append("%srt_move_toward(self, %s, %s, %s);" %
                     (pad, self._val(a.get("x"), vars_map),
                      self._val(a.get("y"), vars_map),
                      self._val(a.get("speed"), vars_map)))
        elif k == "change_sprite":
            si = self.spr_ix.get(a.get("sprite"))
            if si is not None:
                L.append("%sself->sprite = %d; self->image_index = 0;" % (pad, si))
            else:
                self._missing("Change Sprite", "sprite", a.get("sprite") or a.get("_was"))
        elif k == "set_image_speed":
            L.append("%sself->image_speed = %s;" %
                     (pad, self._val(a.get("value"), vars_map)))
        elif k == "destroy_object":
            oi = self.obj_ix.get(a.get("object"))
            if oi is not None:
                L.append("%srt_destroy_object(%d);" % (pad, oi))
            else:
                self._missing("Destroy Object", "object", a.get("object") or a.get("_was"))
        elif k in ("set_score", "set_lives", "set_health"):
            g = {"set_score": "nb_score", "set_lives": "nb_lives",
                 "set_health": "nb_health"}[k]
            L.append("%s%s = %s;" % (pad, g, self._val(a.get("value"), vars_map)))
        elif k in ("add_score", "add_lives", "add_health"):
            g = {"add_score": "nb_score", "add_lives": "nb_lives",
                 "add_health": "nb_health"}[k]
            if k == "add_health":
                # The floor: health never goes below zero through the action.
                # Raw C keeps raw access; the engine re-clamps at the end of
                # every step, so even that cannot LEAVE it negative.
                L.append("%sif ((%s += %s) < 0) %s = 0;"
                         % (pad, g, self._val(a.get("value"), vars_map), g))
            else:
                L.append("%s%s += %s;" % (pad, g, self._val(a.get("value"), vars_map)))
        elif k in ("if_score", "if_lives", "if_health"):
            g = {"if_score": "nb_score", "if_lives": "nb_lives",
                 "if_health": "nb_health"}[k]
            op = CMP_OP.get(str(a.get("op", "==")), "==")
            L.append("%sif (%s %s %s) {" %
                     (pad, g, op, self._val(a.get("value"), vars_map)))
            for c in a.get("children") or []:
                L += self._emit_action(c, vars_map, ind + 1)
            L.append("%s}" % pad)
        elif k == "draw_text":
            L.append("%srt_draw_text((%s) / 8, (%s) / 8, %s);" %
                     (pad, self._val(a.get("x"), vars_map),
                      self._val(a.get("y"), vars_map), _cstr(a.get("text", ""))))
        elif k == "draw_number":
            L.append("%srt_draw_int((%s) / 8, (%s) / 8, %s);" %
                     (pad, self._val(a.get("x"), vars_map),
                      self._val(a.get("y"), vars_map),
                      self._val(a.get("value"), vars_map)))
        elif k == "glide":
            L.append("%srt_glide(self, %s, %s, %s);"
                     % (pad, self._val(a.get("x"), vars_map),
                        self._val(a.get("y"), vars_map),
                        self._val(a.get("frames"), vars_map)))
        elif k == "input_lock":
            L.append("%srt_input_lock(%d);"
                     % (pad, 0 if str(a.get("on", "on")).lower() == "off" else 1))
        elif k == "menu":
            lines = [str(a.get(key) or "").strip()
                     for key in ("a", "b", "c", "d")]
            lines = [x for x in lines if x]
            slot = vars_map.get(str(a.get("var", "")).strip())
            if not lines:
                self._problem(_t("%s - Show Menu has no lines in it, so there is "
                              "nothing to choose from.")
                              % (self._where or "?"))
            elif slot is None:
                # Without somewhere to put the answer the menu opens and the
                # choice is thrown away, which looks like the menu not working.
                self._problem(_t("%s - Show Menu does not say which variable the "
                              "answer goes in, so the choice would be lost.")
                              % (self._where or "?"))
            else:
                self._menu_n += 1
                name = self._unique_c("menu_%d" % self._menu_n)
                name = "nb_" + name if not name.startswith("nb_") else name
                self.menus.append((name, lines))
                L.append("%srt_menu_open_var(%s, %d, 3, 3, %d, self, %d);"
                         % (pad, name, len(lines),
                            max(6, max(len(x) for x in lines) + 3), slot))
        elif k == "say":
            L.append("%srt_say(%s);" % (pad, _cstr(str(a.get("text") or ""))))
        elif k == "clear_text":
            L.append("%srt_clear_text();" % pad)
        elif k == "save_game":
            L.append("%srt_game_save();" % pad)
        elif k == "load_game":
            L.append("%srt_game_load();" % pad)
        elif k == "execute_code":
            if str(a.get("lang") or "").strip().lower() == "c":
                # Verbatim, inside the event's function. No checking here: the
                # compiler is the authority on C and its message names the line.
                # The block is fenced so that message can be traced back to the
                # row it came from.
                code = a.get("code")
                if isinstance(code, str) and code.strip():
                    L.append("%s/* C: %s */" %
                             (pad, (self._where or "action").replace("*/", "* /")))
                    L.append("%s{" % pad)
                    for line in code.split("\n"):
                        L.append((pad + "    " + line) if line.strip() else "")
                    L.append("%s}" % pad)
                return L
            try:
                L += _Gml(self, vars_map, self.global_ix).compile(
                    a.get("code", ""), ind)
            except GmlError as e:
                L.append("%s/* script error: %s */" %
                         (pad, str(e).replace("*/", "* /")))
                where = self._where
                if getattr(e, "line", 0):
                    where += " · " + ("line %d" % e.line)
                self.problems.append("%s — %s" % (where, e) if where else str(e))
        return L

    @staticmethod
    def _event_name(ev):
        """A human name for an event, matching the IDE's event list, so a
        problem can say WHERE it is in words the author recognises."""
        t = (ev or {}).get("type")
        if t == "key":
            return "Key %s" % (ev.get("key") or "?")
        if t == "keypress":
            return "Press %s" % (ev.get("key") or "?")
        if t == "keyrelease":
            return "Release %s" % (ev.get("key") or "?")
        if t == "alarm":
            return "Alarm %s" % (ev.get("alarm") if ev.get("alarm") is not None
                                 else "?")
        if t == "collision":
            return "Collide with %s" % (ev.get("object") or "?")
        return (t or "?").capitalize()

    def _emit_event_body(self, ev, vars_map):
        # Remember where we are so a script mistake is reported against the object
        # and event the author can actually click on.
        self._where = "%s · %s" % (self._obj_id or "?", self._event_name(ev))
        L = []
        for a in (ev or {}).get("actions") or []:
            L += self._emit_action(a, vars_map, 1)
        self._where = ""
        return L

    # What a column type becomes in C. Kept here rather than imported from the
    # editor so the generator does not depend on the GUI module.
    COLUMN_C = {"int": "s32", "text": "const char*", "bool": "u8"}

    def _emit_affine_tiles(self):
        """The affine tileset: 8 bits per pixel, one byte a pixel, 64 bytes a
        tile — four times the 4bpp tiles beside it, which is the price of an
        affine layer's 256 colours and why this is a SEPARATE tileset rather
        than a conversion of the room's own.

        The frames arrive as 8x8 BGR555 pixel lists like every other tileset;
        the colours are matched into one shared 256-entry palette. Emitted as
        u16 pairs because that is what the runtime's DMA copy takes.
        """
        src = self.m.get("affine_tileset") or {}
        frames = src.get("tiles") if isinstance(src, dict) else None
        if not frames:
            # All three symbols, always. The runtime's reference to the
            # palette sits inside `if (nb_aff_tile_count > 0)`, which is a
            # RUNTIME condition -- the linker still demands the symbol, so
            # omitting it here failed every project WITHOUT an affine
            # tileset while the one project with one linked fine.
            self.w("const u16 nb_aff_tiles[] = { 0 };")
            self.w("const int nb_aff_tile_count = 0;")
            self.w("const u16 nb_aff_palette[256] = { 0 };")
            self.w("")
            return
        pal = [TRANSPARENT & 0x7FFF]
        index = {pal[0]: 0}
        words, over = [], 0
        for tile in frames[:256]:
            px = list(tile)[:64] + [TRANSPARENT] * max(0, 64 - len(tile))
            bytes_ = []
            for c in px:
                c &= 0x7FFF
                if c not in index:
                    if len(pal) < 256:
                        index[c] = len(pal)
                        pal.append(c)
                    else:
                        over += 1
                        index[c] = 0
                bytes_.append(index[c])
            for k in range(0, 64, 2):
                words.append(bytes_[k] | (bytes_[k + 1] << 8))
        if over:
            self._problem(_t("The affine tiles use more than 256 colours; "
                             "%d pixels were left blank") % over)
        if len(frames) > 256:
            self._problem(_t("An affine tileset holds 256 tiles; %d were "
                             "dropped") % (len(frames) - 256))
        self.w("const u16 nb_aff_tiles[] = { %s };"
               % ", ".join("0x%04X" % w for w in words))
        self.w("const int nb_aff_tile_count = %d;" % (len(words) // 32))
        # The affine layer reads the BG palette, so its colours go there --
        # past entry 15, which the 4bpp tiles cannot reach anyway.
        self.w("const u16 nb_aff_palette[256] = { %s };"
               % ", ".join("0x%04X" % (pal[i] if i < len(pal) else 0)
                           for i in range(256)))
        self.w("")

    def _emit_name_constants(self):
        """Names for the things the editors made, so inline C can say them.

        Actions resolve an object to its index in nb_objects[] and emit the
        bare number -- `rt_meeting(self, 1)`. Inline C had nothing to resolve
        with, so an author writing bespoke behaviour had to hard-code that 1,
        and reordering or deleting an object silently repointed it at a
        different one. The action layer and Execute Code each worked and did
        not compose.

        The generated names are the author's, run through the same identifier
        rules and the same collision check as everything else, so two objects
        called "Bone" and "bone!" cannot collide and none of them can land on
        a C keyword or a name the runtime owns.
        """
        groups = (("NB_OBJ",  self.objects,  "obj"),
                  ("NB_SPR",  self.sprites,  "spr"),
                  ("NB_SND",  self.sounds,   "snd"),
                  ("NB_ROOM", self.rooms,    "room"))
        any_emitted = False
        for prefix, items, fallback in groups:
            if not items:
                continue
            if not any_emitted:
                self.w("/* Names for what the editors made. Indices shift when "
                       "things are")
                self.w("   reordered; these do not. */")
                any_emitted = True
            seen = {}
            for i, it in enumerate(items):
                base = self._c_ident(it.get("name") or it.get("id") or "",
                                     fallback).upper()
                name = "%s_%s" % (prefix, base)
                n = 2
                while name in seen:            # two objects, one author name
                    name = "%s_%s_%d" % (prefix, base, n)
                    n += 1
                seen[name] = i
                self.w("#define %-28s %d" % (name, i))

    @staticmethod
    def _c_ident(name, fallback):
        """A C identifier from an author's column or table name.

        Authors write "Base HP" and "attack%"; C takes neither. Rewritten
        rather than rejected, because a table that refuses a space in a heading
        is a table nobody finishes filling in."""
        out = []
        for ch in str(name or ""):
            out.append(ch if (ch.isalnum() or ch == "_") else "_")
        ident = "".join(out).strip("_")
        if not ident or ident[0].isdigit():
            ident = fallback + ("_" + ident if ident else "")
        if ident in C_KEYWORDS:
            ident += "_"
        return ident

    def gen_tables(self):
        """Each table as a struct and an array of it, plus a count.

        The count is emitted beside the array because C cannot ask an array its
        length once it has decayed to a pointer, and a game that hard-codes the
        row count is a game that reads past the end the first time a row is
        added."""
        tables = self.m.get("tables") or []
        if not tables:
            return
        self.w("/* ---- tables ---- */")
        for ti, t in enumerate(tables):
            if not isinstance(t, dict):
                continue
            name = self._unique_c(
                self._c_ident(t.get("name") or t.get("id"), "table%d" % ti),
                ("nb_row_%s", "nb_%s", "nb_%s_count"))
            cols = [c for c in (t.get("columns") or []) if isinstance(c, dict)]
            if not cols:
                continue
            fields, seen = [], set()
            for ci, c in enumerate(cols):
                fn = self._c_ident(c.get("name"), "col%d" % ci)
                while fn in seen:
                    fn += "_"
                seen.add(fn)
                fields.append((fn, c.get("type") if c.get("type") in
                               self.COLUMN_C else "text"))
            self.w("typedef struct {")
            for fn, ty in fields:
                self.w("    %s %s;" % (self.COLUMN_C[ty], fn))
            self.w("} nb_row_%s;" % name)
            self.w("const nb_row_%s nb_%s[] = {" % (name, name))
            for row in t.get("rows") or []:
                vals = []
                for ci, (fn, ty) in enumerate(fields):
                    v = row[ci] if isinstance(row, list) and ci < len(row) else None
                    if ty == "text":
                        vals.append(_cstr("" if v is None else str(v)))
                    elif ty == "bool":
                        vals.append("1" if v in (1, True, "1", "true", "yes")
                                    else "0")
                    else:
                        vals.append(str(_int(v, 0)))
                self.w("    { %s }," % ", ".join(vals))
            self.w("};")
            self.w("const int nb_%s_count = %d;"
                   % (name, len(t.get("rows") or [])))
            self.w("")

    def gen_scripts(self):
        """File-scope C, verbatim, one block per script.

        Emitted verbatim rather than parsed: a script IS C, and the level-3
        promise in the spec is that an expert writes C and gets C. A mistake in
        one is a compiler error, reported against the script by name -- which is
        why each block is fenced with a comment carrying that name."""
        scripts = self.m.get("scripts") or []
        if not scripts:
            return
        self.w("/* ---- scripts ---- */")
        for sc in scripts:
            if not isinstance(sc, dict):
                continue
            code = sc.get("code")
            if not isinstance(code, str) or not code.strip():
                continue
            name = sc.get("name") or sc.get("id") or "script"
            self.w("/* %s */" % str(name).replace("*/", ""))
            for line in code.split("\n"):
                self.w(line)
            self.w("")

    def gen_menu_arrays(self):
        """The item lists, at file scope.

        The menu holds the pointer rather than copying the strings, so an array
        built inside an event function would be gone by the time the menu drew
        it."""
        if not self.menus:
            return
        self.w("/* ---- menu lines ---- */")
        for name, lines in self.menus:
            self.w("static const char *const %s[] = { %s };"
                   % (name, ", ".join(_cstr(x) for x in lines)))
        self.w("")

    def gen_objects(self):
        # per-object event functions
        for i, o in enumerate(self.objects):
            cid = _cid(o.get("id"), "obj")
            self._obj_id = o.get("id") or cid
            vars_map = self._collect_vars(o)
            evs = o.get("events") or []
            create = [e for e in evs if e.get("type") == "create"]
            steps = [e for e in evs if e.get("type") == "step"]
            keys = [e for e in evs if e.get("type") == "key"]
            keyps = [e for e in evs if e.get("type") == "keypress"]
            keyrels = [e for e in evs if e.get("type") == "keyrelease"]
            colls = [e for e in evs if e.get("type") == "collision"]
            alarms = [e for e in evs if e.get("type") == "alarm"]
            destroys = [e for e in evs if e.get("type") == "destroy"]
            no_healths = [e for e in evs if e.get("type") == "no_health"]

            def emit_fn(name, events):
                self.w("static void %s(Instance* self) {" % name)
                self.w("    (void)self;")
                for e in events:
                    for ln in self._emit_event_body(e, vars_map):
                        self.w(ln)
                self.w("}")

            if create:
                emit_fn("%s_create" % cid, create)
            if no_healths:
                emit_fn("%s_no_health" % cid, no_healths)
            if destroys:
                emit_fn("%s_destroy" % cid, destroys)
            # step fn folds in step + alarm + key(held/press/release) + collision
            has_step = bool(steps or keys or keyps or keyrels or colls or alarms)
            if has_step:
                self.w("static void %s_step(Instance* self) {" % cid)
                self.w("    (void)self;")
                for e in steps:
                    for ln in self._emit_event_body(e, vars_map):
                        self.w(ln)
                for e in alarms:
                    n = _int(e.get("alarm"), 0)
                    if not (0 <= n < 4):
                        continue
                    self.w("    if (self->alarm[%d] == 0) {" % n)
                    self.w("        self->alarm[%d] = -1;" % n)
                    for ln in self._emit_event_body(e, vars_map):
                        self.w("    " + ln)
                    self.w("    }")
                for grp, fn in ((keys, "rt_key_held"), (keyps, "rt_key_pressed"),
                                (keyrels, "rt_key_released")):
                    for e in grp:
                        mac = KEY_MACRO.get(str(e.get("key", "")).lower())
                        if not mac:
                            continue
                        self.w("    if (%s(%s)) {" % (fn, mac))
                        for ln in self._emit_event_body(e, vars_map):
                            self.w("    " + ln)
                        self.w("    }")
                for e in colls:
                    oi = self.obj_ix.get(e.get("object"))
                    if oi is None:
                        # The whole body of the event is dropped here, which is
                        # the most expensive silent loss in the compiler.
                        self.problems.append(
                            "%s \u00b7 %s - the object it collided with "
                            "(\u201c%s\u201d) is not in the project any more, "
                            "so nothing in this event will happen."
                            % (self._obj_id or "?", self._event_name(e),
                               e.get("object") or e.get("_was") or "?"))
                        continue
                    self.w("    if (rt_meeting(self, %d)) {" % oi)
                    for ln in self._emit_event_body(e, vars_map):
                        self.w("    " + ln)
                    self.w("    }")
                self.w("}")
            # Keep what the object table needs to know in OUR OWN map. These
            # four used to be written back into the caller's object dicts --
            # which the SDK autosaves, so every .gbaproj on disk grew _cid,
            # _has_create, _has_step and _has_destroy the first time it was
            # exported. A generator must not write to the document it reads.
            self._fns[id(o)] = (cid, bool(create), has_step, bool(destroys),
                                bool(no_healths))
        self.w("")
        # object table: { sprite, visible, solid, create, step, draw, destroy }
        self.w("const nb_Object nb_objects[] = {")
        for o in self.objects:
            (cid, has_create, has_step, has_destroy,
             has_no_health) = self._fns.get(
                id(o), (_cid(o.get("id"), "obj"), False, False, False, False))
            spr = self.spr_ix.get(o.get("sprite"), -1)
            worn = o.get("sprite") or o.get("_was")
            if spr < 0 and worn:
                self.problems.append(
                    "%s - the sprite \u201c%s\u201d it wears is not in the "
                    "project any more, so it will be invisible."
                    % (o.get("id") or "?", worn))
            vis = 0 if o.get("visible") is False else 1
            solid = 1 if o.get("solid") else 0
            cfn = "%s_create" % cid if has_create else "0"
            sfn = "%s_step" % cid if has_step else "0"
            dfn = "%s_destroy" % cid if has_destroy else "0"
            # Fields past `destroy` were never emitted, so no object ever had
            # a drawing depth, a collision box, or -- the one that matters --
            # tilecol. The runtime returns early when tilecol is 0 and moves
            # the instance without consulting the tile layer at all, which is
            # why emitting the solid-tile table alone changed nothing.
            depth = max(0, min(7, _int(o.get("depth"), 0)))
            tilecol = max(0, min(2, _int(o.get("tilecol"), 0)))
            inset = max(0, min(64, _int(o.get("bb_inset"), 0)))
            bb = [max(0, min(64, _int(o.get(k), inset)))
                  for k in ("bb_l", "bb_t", "bb_r", "bb_b")]
            hurt = max(0, min(255, _int(o.get("hurt_frames"), 0)))
            nfn = "%s_no_health" % cid if has_no_health else "0"
            self.w("    { %d, %d, %d, %s, %s, 0, %s, %d, %d, %d, %d, %d, %d, "
                   "%d, %s },"
                   % (spr, vis, solid, cfn, sfn, dfn, depth, tilecol,
                      bb[0], bb[1], bb[2], bb[3], hurt, nfn))
        if not self.objects:
            self.w("    { -1, 1, 0, 0, 0, 0, 0 },")
        self.w("};")
        self.w("const int nb_object_count = %d;" % len(self.objects))
        self.w("")

    # ---- rooms ----
    def gen_rooms(self):
        has_tiles = {}
        has_far = {}
        has_warps = {}
        has_aff = {}
        for i, r in enumerate(self.rooms):
            insts = r.get("instances") or []
            self.w("static const nb_InstanceDef room_%d_insts[] = {" % i)
            for it in insts:
                oi = self.obj_ix.get(it.get("object"))
                if oi is None:
                    self.problems.append(
                        "%s - it places an object called \u201c%s\u201d, and "
                        "there is no such object any more, so nothing will be "
                        "there." % (r.get("id") or "?",
                                    it.get("object") or it.get("_was") or "?"))
                if oi is None:
                    continue
                self.w("    { %d, %d, %d }," %
                       (oi, _int(it.get("x")), _int(it.get("y"))))
            self.w("    { -1, 0, 0 },")
            self.w("};")
            # An optional AFFINE ground layer: one 8-bit tile index per
            # cell, 16x16 or 32x32. A room carrying one gives up its flat
            # tile layer and its parallax layer -- mode 1 has two text
            # backgrounds where mode 0 has four, and the runtime spends
            # those on the dialogue panel and the text over it.
            am = r.get("affine")
            if am:
                cells = 1024 if len(am) > 256 else 256
                flat = [max(0, min(255, _int(t))) for t in am]
                flat = (flat + [0] * cells)[:cells]
                if r.get("tiles"):
                    self._problem(
                        _t("%s has both a flat tile layer and an affine "
                           "one; only the affine layer will show")
                        % (r.get("name") or r.get("id") or "A room"))
                self.w("static const u8 room_%d_aff[] = { %s };"
                       % (i, ", ".join(str(v) for v in flat)))
                has_aff[i] = cells
            # optional BG tile layer: (w/8)*(h/8) tile indices, row-major, 0=empty
            tm = r.get("tiles")
            if tm:
                cw = max(16, _int(r.get("w"), 240)) // 8
                ch = max(16, _int(r.get("h"), 160)) // 8
                cells = cw * ch
                flat = [max(0, _int(t)) & 0x03FF for t in tm]
                flat = (flat + [0] * cells)[:cells]
                self.w("static const u16 room_%d_tiles[] = { %s };"
                       % (i, ", ".join(str(v) for v in flat)))
                has_tiles[i] = True
            # Room-to-room links. A warp naming a room that no longer exists
            # is dropped and REPORTED: silently keeping it would build a door
            # that does nothing, which is indistinguishable from a door placed
            # in the wrong spot.
            warps = []
            for wp in r.get("warps") or []:
                if not isinstance(wp, dict):
                    continue
                dest = self.room_ix.get(wp.get("room"))
                if dest is None:
                    self.problems.append(
                        "%s - a doorway leads to a room called \u201c%s\u201d, "
                        "and there is no such room any more, so it will not "
                        "go anywhere."
                        % (r.get("name") or r.get("id") or "?",
                           wp.get("room") or wp.get("_was") or "?"))
                    continue
                warps.append((max(0, _int(wp.get("x"))), max(0, _int(wp.get("y"))),
                              max(1, _int(wp.get("w"), 16)),
                              max(1, _int(wp.get("h"), 16)), dest,
                              max(0, _int(wp.get("tx"))),
                              max(0, _int(wp.get("ty")))))
            if warps:
                self.w("static const nb_Warp room_%d_warps[] = {" % i)
                for x, y, ww, hh, dest, tx, ty in warps:
                    self.w("    { %d, %d, %d, %d, %d, %d, %d },"
                           % (x, y, ww, hh, dest, tx, ty))
                self.w("};")
                has_warps[i] = len(warps)

            # The parallax layer: a 32x32 repeating map on BG3, drawn behind
            # everything and scrolled at a fraction of the camera. Fixed size,
            # because the hardware wraps it -- a bigger one would not tile.
            far = r.get("far")
            if far:
                ff = [max(0, _int(t)) & 0x03FF for t in far]
                ff = (ff + [0] * 1024)[:1024]
                self.w("static const u16 room_%d_far[] = { %s };"
                       % (i, ", ".join(str(v) for v in ff)))
                has_far[i] = True
        self.w("")
        self.w("const nb_Room nb_rooms[] = {")
        for i, r in enumerate(self.rooms):
            w = max(16, _int(r.get("w"), 240))
            h = max(16, _int(r.get("h"), 160))
            bg = _rgb15(r.get("bg"), 0)
            speed = max(1, min(60, _int(r.get("speed"), 60)))
            n = len([it for it in (r.get("instances") or [])
                     if self.obj_ix.get(it.get("object")) is not None])
            tiles_ref = ("room_%d_tiles" % i) if has_tiles.get(i) else "0"
            # Fields past `tiles` were all left zero, which is why three
            # finished runtime features -- tile collision, the parallax layer
            # and the open-edge room -- were unreachable from the tool.
            solid_ref = ("nb_tile_solid"
                         if getattr(self, "_has_solid", False)
                         and has_tiles.get(i) else "0")
            far_ref = ("room_%d_far" % i) if has_far.get(i) else "0"
            far_div = max(1, min(8, _int(r.get("far_div"), 2)))
            edge_open = 1 if r.get("edge_open") else 0
            warp_ref = ("room_%d_warps" % i) if has_warps.get(i) else "0"
            aff_ref = ("room_%d_aff" % i) if has_aff.get(i) else "0"
            aff_size = 1 if has_aff.get(i) == 1024 else 0
            self.w("    { %d, %d, 0x%04X, %d, %d, room_%d_insts, %s, %s, %s, "
                   "%d, %d, %s, %d, %s, %d }," %
                   (w, h, bg, speed, n, i, tiles_ref, solid_ref, far_ref,
                    far_div, edge_open, warp_ref, has_warps.get(i, 0),
                    aff_ref, aff_size))
        if not self.rooms:
            self.w("    { 240, 160, 0x0000, 60, 0, 0, 0 },")
        self.w("};")
        self.w("const int nb_room_count = %d;" % len(self.rooms))
        start = self.room_ix.get(self.m.get("start_room"), 0)
        self.w("const int nb_start_room = %d;" % (start if self.rooms else 0))
        self.w("")

    # ---- sounds ----
    def gen_sounds(self):
        has_drum = {}
        has_pcm = {}
        for i, s in enumerate(self.sounds):
            lead = [max(0, min(255, _int(n, 0))) for n in (s.get("lead") or [])]
            bass = [max(0, min(255, _int(n, 0))) for n in (s.get("bass") or [])]
            n = max(len(lead), len(bass), 1)
            lead = (lead + [0] * n)[:n]
            bass = (bass + [0] * n)[:n]
            self.w("static const u8 snd_lead_%d[] = { %s };"
                   % (i, ", ".join(str(v) for v in lead)))
            self.w("static const u8 snd_bass_%d[] = { %s };"
                   % (i, ", ".join(str(v) for v in bass)))
            # The drum track drives the NOISE channel, which no built game has
            # ever used: the initialiser stopped at `bass`, so this and the
            # four settings after it were zero-filled and the runtime read
            # defaults it was never given.
            # A sampled sound: signed 8-bit at 16384 Hz, converted on import.
            # ROM is 4-byte aligned and the FIFO takes 32 bits at a time, so
            # the length is padded to a multiple of four -- a short final word
            # would send whatever follows the array to the speaker.
            pcm = s.get("pcm")
            if isinstance(pcm, list) and len(pcm) >= 16:
                b = [max(-128, min(127, _int(v, 0))) for v in pcm]
                while len(b) % 4:
                    b.append(0)
                self.w("static const signed char snd_pcm_%d[] = { %s };"
                       % (i, ", ".join(str(v) for v in b)))
                has_pcm[i] = len(b)
            drum = [max(0, min(4, _int(v, 0))) for v in (s.get("drum") or [])]
            if any(drum):
                drum = (drum + [0] * n)[:n]
                self.w("static const u8 snd_drum_%d[] = { %s };"
                       % (i, ", ".join(str(v) for v in drum)))
                has_drum[i] = True
        self.w("")
        self.w("const nb_Sound nb_sounds[] = {")
        for i, s in enumerate(self.sounds):
            tempo = max(1, min(60, _int(s.get("tempo"), 6)))
            loop = 1 if s.get("loop") else 0
            n = max(len(s.get("lead") or []), len(s.get("bass") or []), 1)
            drum_ref = ("snd_drum_%d" % i) if has_drum.get(i) else "0"
            # kind 1 is a sound effect: it plays on the wave channel and layers
            # OVER the music instead of stopping it. Left at 0, every effect in
            # every game silenced the music for its duration.
            kind = 1 if str(s.get("kind", "")).lower() in ("1", "sfx",
                                                           "effect") else 0
            duty = max(0, min(4, _int(s.get("duty"), 0)))
            vol = max(0, min(15, _int(s.get("vol"), 0)))
            decay = max(0, min(7, _int(s.get("decay"), 0)))
            prio = max(0, min(7, _int(s.get("prio"), 0)))
            pcm_ref = ("snd_pcm_%d" % i) if has_pcm.get(i) else "0"
            # A sampled sound marked loop is a soundtrack: the runtime plays
            # it on the second PCM voice, looping, under one-shot effects.
            pcm_loop = 1 if (has_pcm.get(i) and loop) else 0
            self.w("    { %d, %d, %d, snd_lead_%d, snd_bass_%d, %s, %d, %d, "
                   "%d, %d, %d, %s, %d, %d },"
                   % (tempo, loop, n, i, i, drum_ref, kind, duty, vol, decay,
                      prio, pcm_ref, has_pcm.get(i, 0), pcm_loop))
        if not self.sounds:
            self.w("    { 6, 0, 0, 0, 0 },")
        self.w("};")
        self.w("const int nb_sound_count = %d;" % len(self.sounds))
        self.w("")

    def generate(self):
        self.w("/* auto-generated by the Notebook OS GBA SDK — do not edit */")
        self.w('#include "runtime.h"')
        self.w("")
        # Before anything else: inline C in a SCRIPT is emitted further down
        # but may name any object, sprite, sound or room, so the names have to
        # be in scope from the first line of user code.
        self._emit_name_constants()
        # The save part, chosen per project. Emulators and flash carts size
        # the battery by SCANNING the ROM for one of these signature strings,
        # so exactly one may exist -- which is why the runtime no longer bakes
        # its SRAM string in and this is the only place one is written.
        st = {"sram": 0, "flash64": 1, "flash128": 2,
              "eeprom512": 3, "eeprom8k": 4}.get(
            str(self.m.get("save_type") or "sram").strip().lower())
        if st is None:
            self._problem(_t("save_type must be sram, flash64, flash128, "
                             "eeprom512 or eeprom8k"))
            st = 0
        sig = {0: "SRAM_V113", 1: "FLASH512_V131", 2: "FLASH1M_V102",
               3: "EEPROM_V122", 4: "EEPROM_V122"}[st]
        self.w("const int nb_save_type = %d;" % st)
        self.w('const char nb_save_sig[] __attribute__((used, aligned(4))) = "%s";'
               % sig)
        self.w("")
        self.gen_sprites()
        self.gen_bg()
        self.gen_sounds()
        # Scripts come BEFORE objects: C requires a function to be declared
        # before it is called, and the whole point of a script is being called
        # from an object's events.
        # Tables before scripts, and scripts before objects: each may use what
        # the one before it declared, and C wants a declaration first.
        self.gen_tables()
        self.gen_scripts()
        # The object bodies are generated first so their menus are known, then
        # written after the arrays they point at.
        body_at = len(self.out)
        self.gen_objects()
        head = self.out[body_at:]
        del self.out[body_at:]
        self.gen_menu_arrays()
        self.out.extend(head)
        self.gen_rooms()
        return "\n".join(self.out) + "\n"


# What the hardware actually has. Named here rather than written into the
# messages, because a limit quoted in three places is a limit that will one day
# disagree with itself.
BUDGET = {
    "obj_tiles":  1024,     # 32 KB of OBJ VRAM at 4bpp
    "bg_tiles":    512,     # charblock 0
    "obj_banks":    16,     # 16-colour sprite palettes
    "instances":   128,
    "oam":         128,
    "globals":      32,
    "rom":    32 * 1024 * 1024,
}


def budget_report(model):
    """What this project costs against what the console has.

    The point is NOT a total. A project that will not fit needs to know WHICH
    ASSET to blame, so every line carries the largest contributors -- "over by
    40 tiles" is a fact nobody can act on, and "Boss is 64x64 with 12 frames,
    which is 192 of them" is a decision.

    Estimates are marked as estimates. ROM in particular cannot be known
    without compiling, and a confident wrong number is worse than a range."""
    m = model if isinstance(model, dict) else {}
    lines = []

    # --- sprite tiles: the one that actually runs out ---
    spr_cost = []
    for s in m.get("sprites") or []:
        if not isinstance(s, dict):
            continue
        w = max(8, _int(s.get("w"), 16))
        h = max(8, _int(s.get("h"), w))
        frames = len(s.get("frames") or []) or 1
        per = ((w + 7) // 8) * ((h + 7) // 8)
        # Two whole sentences, not a "%s" plural slot: nbi18n hands back the
        # English whenever a translation's placeholders differ from the
        # source's, and most languages do not form a plural by adding -s.
        detail = (_t("%dx%d, one frame") % (w, h) if frames == 1
                  else _t("%dx%d, %d frames") % (w, h, frames))
        spr_cost.append((per * frames, s.get("name") or s.get("id") or "?",
                         detail))
    spr_cost.sort(reverse=True)
    obj_used = sum(c for c, _n, _d in spr_cost)

    # --- background tiles ---
    bg_cost = []
    for t in m.get("tilesets") or []:
        if not isinstance(t, dict):
            continue
        size = _int(t.get("size"), 8) or 8
        per = max(1, (size // 8) ** 2)
        n = len(t.get("tiles") or [])
        bg_cost.append((per * n, t.get("name") or t.get("id") or "?",
                        _t("%d tiles at %dx%d") % (n, size, size)))
    bg_cost.sort(reverse=True)
    bg_used = sum(c for c, _n, _d in bg_cost) + 1     # +1 for the blank tile

    # --- sampled audio, which is where ROM goes ---
    pcm_cost = []
    for s in m.get("sounds") or []:
        if not isinstance(s, dict):
            continue
        n = len(s.get("pcm") or [])
        if n >= 16:
            pcm_cost.append((n, s.get("name") or s.get("id") or "?",
                             _t("%.1f seconds") % (n / 16384.0)))
    pcm_cost.sort(reverse=True)

    # --- instances placed in one room ---
    room_cost = []
    for r in m.get("rooms") or []:
        if not isinstance(r, dict):
            continue
        room_cost.append((len(r.get("instances") or []),
                          r.get("name") or r.get("id") or "?", ""))
    room_cost.sort(reverse=True)

    pal = palette_report(m)

    def line(name, used, cap, worst, unit="", note=""):
        return {"name": name, "used": used, "cap": cap,
                "over": used > cap, "unit": unit, "note": note,
                "worst": [{"cost": c, "name": n, "detail": d}
                          for c, n, d in worst[:3]]}

    lines.append(line("Sprite tiles", obj_used, BUDGET["obj_tiles"], spr_cost,
                      note="every frame of every sprite is in memory at once"))
    lines.append(line("Background tiles", bg_used, BUDGET["bg_tiles"], bg_cost))
    lines.append(line("Sprite colour sets",
                      pal.get("wanted", pal["used"]), BUDGET["obj_banks"],
                      [(len(b["sprites"]), _t("set %d") % b["index"],
                        ", ".join(b["sprites"])) for b in pal["banks"]]))
    lines.append(line("Objects in a room",
                      room_cost[0][0] if room_cost else 0,
                      BUDGET["instances"], room_cost,
                      note="objects created while playing count too"))
    if pcm_cost:
        lines.append(line("Sampled audio", sum(c for c, _n, _d in pcm_cost),
                          BUDGET["rom"], pcm_cost, unit="bytes",
                          note="16 KB per second"))
    return {"lines": lines,
            "over": [l for l in lines if l["over"]],
            "problems": pal["problems"]}


def palette_report(model):
    """What the build will do with this project's colours, before building it.

    The spec names palettes as the constraint every GBA project eventually
    hits, and says a tool that hides it badly produces games that look wrong
    and authors who cannot find out why. Hiding it badly is what the tool did:
    the allocator already refused to overflow and already reported it, but the
    report only appeared in a build log, after the fact, phrased per sprite. It
    never said how much room was left, which sprites were sharing, or which one
    was about to cost the sixteenth bank.

    Runs the REAL allocator -- the same call the generator makes -- so the
    report cannot describe a different allocation than the one that ships.

    Returns:
      banks    [{"index", "colours"[], "sprites"[], "free"}]  used banks only
      sprites  [{"index", "id", "name", "bank", "colours", "over"}]
      used     banks in use, of 16
      total    colours placed, of 240
    """
    g = _Gen(model if isinstance(model, dict) else {})
    g._build_obj_palette()
    banks = getattr(g, "_banks", []) or []
    by_bank = {}
    sprites = []
    for si, s in enumerate(g.sprites):
        cols = []
        for fr in s.get("frames") or []:
            for px in fr:
                c = _int(px, TRANSPARENT) & 0x7FFF
                if c != (TRANSPARENT & 0x7FFF) and c not in cols:
                    cols.append(c)
        bank = g._spr_bank.get(si, 0)
        name = s.get("name") or s.get("id") or ("Sprite %d" % (si + 1))
        sprites.append({"index": si, "id": s.get("id"), "name": name,
                        "bank": bank, "colours": len(cols),
                        "over": max(0, len(cols) - 15),
                        "pinned": isinstance(s.get("pal_bank"), int)})
        by_bank.setdefault(bank, []).append(name)
    out = []
    for bi, bank in enumerate(banks):
        if not bank and bi not in by_bank:
            continue
        ordered = [0] * 16
        for colour, idx in bank.items():
            if 0 <= idx < 16:
                ordered[idx] = colour
        out.append({"index": bi, "colours": ordered,
                    "sprites": by_bank.get(bi, []),
                    "used": len(bank), "free": 15 - len(bank)})
    return {"banks": out,
            "sprites": sprites,
            "used": len(out),
            # What the project ASKED for, which is what a budget line has to
            # show: `used` is capped at the 16 that exist and can never be over.
            "wanted": len(out) + getattr(g, "_banks_wanted", 0),
            "total": sum(b["used"] for b in out),
            "problems": list(g.problems)}


def preview_event_c(model, obj, ev):
    """The C one event compiles to, for reading rather than building.

    This exists for teaching. Part 0 of the spec makes one rule binding on every
    level of the tool: any action can show the script it produces, and any script
    the C. A row of drag-drop actions and a page of C are then not two ways of
    working but one thing seen at two depths, and the step up is reading
    something already written rather than starting from nothing.

    Returns (code, problems). Problems are the same ones a build would report,
    so a mistake is visible here before a build is ever run."""
    g = _Gen(model if isinstance(model, dict) else {})
    g.audit_vars = False
    g._obj_id = (obj or {}).get("id") or "?"
    # Slots are allocated across the WHOLE object, so an event previewed in
    # isolation must still be weighed with its siblings or its variables land in
    # different slots here than in the build -- a preview that lies about the
    # thing it is teaching. If the event is not yet saved into the object, add it.
    o = dict(obj or {})
    evs = list(o.get("events") or [])
    if ev is not None and not any(e is ev for e in evs):
        evs.append(ev)
    o["events"] = evs
    vars_map = g._collect_vars(o)
    body = g._emit_event_body(ev or {}, vars_map)
    head = "void %s_%s(int i)" % (_cid((obj or {}).get("id"), "obj"),
                                  _cid(_Gen._event_name(ev or {}).lower(), "ev"))
    lines = [head, "{"]
    lines += body or ["    /* no actions */"]
    lines.append("}")
    return "\n".join(lines), list(g.problems)


def generate_c(model):
    return _Gen(model).generate()


def check_project(model):
    """Every mistake in `model` that the compiler would otherwise swallow, as a
    list of plain sentences ready to show the author.

    A line of script the compiler cannot understand used to become a C comment: the
    ROM built, that code silently did nothing, and nobody was told. This runs the
    same generation pass and hands back what it found, so the IDE can say
    "obj_player · Step · line 3 — = does not belong here" before it exports
    something that will not work. Never raises.

    A misspelt NAME is a different case and was not caught for a long time. The
    language gives a slot to any identifier it does not recognise — that is what
    makes `wobble = 7` work without a declaration, and it is also why `hspee =
    2` compiled to a variable nothing reads while the object sat still. It is
    reported now, but as what it is: a variable set and never read, or read and
    never set, with the near-miss named when there is one.
    """
    try:
        g = _Gen(model)
        g.generate()
        return list(g.problems)
    except Exception:
        return []


# ---------------------------------------------------------------- build
def find_gcc(toolchain_dir=TOOLCHAIN_DIR):
    """Locate arm-none-eabi-gcc: the shipped toolchain first, then PATH."""
    cand = os.path.join(toolchain_dir, "bin", "arm-none-eabi-gcc")
    if os.path.isfile(cand):
        return cand
    return shutil.which("arm-none-eabi-gcc")


# A multiboot image runs from EWRAM, and the loader will not send more than
# EWRAM holds. Checked before the build rather than after, because "it linked
# and then nothing happened" is the worst way to learn a size limit.
MULTIBOOT_MAX = 256 * 1024


def _run_capped(cmd, timeout):
    """Run a compiler step with a hard ceiling that actually lands.

    subprocess.run(capture_output=True, timeout=...) kills only the DIRECT
    child on timeout and then goes back to draining its pipes — which gcc's
    own children (cc1, as, ld) still hold open, so the drain blocks forever
    and a capped compile became an eternal "Compiling…" instead of a failure
    (the on-target repeat-build wedge). Start the step in its own session and
    on timeout kill the whole process group, then drain what is left."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, start_new_session=True)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except Exception:
            pass
        try:
            out, err = p.communicate(timeout=10)
        except Exception:
            pass
        raise
    return p.returncode, (out or "") + (err or "")


def build_rom(model, outdir, runtime_dir=RUNTIME_DIR, toolchain_dir=TOOLCHAIN_DIR,
              multiboot=False, trace=None):
    """Compile `model` into <outdir>/game.gba. Returns (ok, gba_path, log).

    multiboot=True builds game.mb instead: the same program linked to run from
    EWRAM, for sending over the link cable to a console with no cartridge.

    `trace`, when given, is called with a short phase name as each stage
    begins — so a build that wedges leaves the NAME of the stage it died in,
    instead of ten silent minutes (the on-target repeat-build wedge sat past
    both subprocess ceilings, which proves nothing without knowing whether
    the worker ever reached them)."""
    def _phase(m):
        if trace is not None:
            try:
                trace(m)
            except Exception:
                pass
    _phase("find_gcc")
    gcc = find_gcc(toolchain_dir)
    if not gcc:
        # ENGLISH, deliberately, and not through _t(): the build log is
        # matched by gbasdk._failure_reason to choose the sentence the
        # author actually reads, which IS translated. Translating this one
        # would stop that match and every failure would report the generic
        # "the compiler stopped part-way through".
        return False, None, "The GBA toolchain (arm-none-eabi-gcc) isn't installed."
    tdir = os.path.dirname(gcc)
    objcopy = os.path.join(tdir, "arm-none-eabi-objcopy")
    problems = []
    # Three different failures, three different reasons. They used to share one
    # message, so a crash INSIDE the generator was reported to the user as "the
    # working files could not be written" — a disk problem they would go and
    # look for and never find.
    try:
        _phase("generate")
        gen = _Gen(model)
        source = gen.generate()
        problems = list(gen.problems)
    except Exception as e:
        # English, and matched by _failure_reason -- see the note above.
        return False, None, ("Could not turn this project into code: %s: %s"
                             % (type(e).__name__, e))
    try:
        _phase("write source")
        os.makedirs(outdir, exist_ok=True)
        with open(os.path.join(outdir, "game_data.c"), "w") as fh:
            fh.write(source)
    except Exception as e:
        # English, and matched by _failure_reason -- see the note above.
        return False, None, "Could not write generated source: %s" % e

    head = ""
    if problems:
        head = ("This game has %d problem%s the compiler could not use:\n  %s\n\n"
                % (len(problems), "" if len(problems) == 1 else "s",
                   "\n  ".join(problems)))
    elf = os.path.join(outdir, "game_mb.elf" if multiboot else "game.elf")
    gba = os.path.join(outdir, "game.mb" if multiboot else "game.gba")
    ldscript = "gba_mb.ld" if multiboot else "gba.ld"
    cmd = [
        gcc, "-mcpu=arm7tdmi", "-mthumb-interwork", "-ffreestanding",
        "-nostdlib", "-O2", "-Wall",
        "-I", runtime_dir,
        "-T", os.path.join(runtime_dir, ldscript),
        os.path.join(runtime_dir, "crt0.s"),
        os.path.join(runtime_dir, "runtime.c"),
        os.path.join(outdir, "game_data.c"),
        # ARM7TDMI has no hardware divide; the compiler emits calls to libgcc's
        # __aeabi_*div helpers, so link libgcc even though we're freestanding.
        "-lgcc",
        # IWRAM holds code AND data in one load segment, so that segment is
        # readable, writable and executable. On a console with no MMU that is
        # what IWRAM IS -- the warning describes a hardening concern that does
        # not exist here. Silenced deliberately rather than left to appear in
        # every build, because a warning nobody can act on is a warning
        # everybody learns to scroll past.
        "-Wl,--no-warn-rwx-segments",
        "-o", elf,
    ]
    log = head + " ".join(cmd) + "\n"
    try:
        _phase("gcc starting (120s ceiling)")
        rc, output = _run_capped(cmd, 120)
        _phase("gcc done rc=%d" % rc)
        log += output
        if rc != 0:
            return False, None, log
    except Exception as e:
        _phase("gcc raised: %s" % type(e).__name__)
        return False, None, log + "\ncompile failed: %s" % e
    try:
        _phase("objcopy starting (60s ceiling)")
        rc, output = _run_capped([objcopy, "-O", "binary", elf, gba], 60)
        _phase("objcopy done rc=%d" % rc)
        log += output
        if rc != 0:
            return False, None, log
        _gbafix(gba, model.get("name") or "")
    except Exception as e:
        _phase("objcopy/gbafix raised: %s" % type(e).__name__)
        return False, None, log + "\nlink/fix failed: %s" % e
    _phase("built %s" % os.path.basename(gba))
    size = os.path.getsize(gba)
    if multiboot and size > MULTIBOOT_MAX:
        # Refused rather than shipped: an oversized image links, writes a file
        # and is then silently not sent, which looks like a cable fault.
        return False, None, log + (
            "\nThis game is %d KB and a link-cable image can be %d KB. "
            "It builds as a cartridge; it cannot be sent over a cable.\n"
            % (size // 1024, MULTIBOOT_MAX // 1024))
    return True, gba, log + "\nBuilt %s (%d bytes)\n" % (gba, size)


# The boot logo bitmap every GBA cartridge must carry at header offset
# 0x04..0x9F. On real hardware the BIOS compares this region against its own
# copy and refuses to start the cartridge if it differs, so a ROM without it
# runs in an emulator but is dead on a console or a flashcart. Every GBA
# homebrew toolchain writes it for exactly this interoperability reason.
#
# Transcribed from two independent open-source toolchains and checked
# byte-for-byte: devkitPro gba-tools `gbafix.c` (a C byte array) and
# jtsiomb/gbasys `crt0.s` (ARM .long words, byte-order converted separately).
# Both yield the same 156 bytes, sha256
# 08a0153cfd6b0ea54b938f7d209933fa849da0d56f5a34c481060c9ff2fad818.
# Do not "tidy" these bytes: a single wrong one fails the check exactly like
# zeros do, but looks correct.
NINTENDO_LOGO = bytes.fromhex(
    "24ffae51699aa2213d84820a84e409ad11248b98c0817f21a352be199309ce20"
    "10464a4af82731ec58c7e83382e3cebf85f4df94ce4b09c194568ac01372a7fc"
    "9f844d73a3ca9a615897a327fc039876231dc7610304ae56bf38840040a70efd"
    "ff52fe036f9530f197fbc08560d68025a963be03014e38e2f9a234ffbb3e0344"
    "780090cb88113a9465c07c6387f03cafd625e48b380aac7221d4f807")
assert len(NINTENDO_LOGO) == 156


def _rom_title(name):
    """A project name as the 12 bytes the cartridge header carries at 0xA0.

    The field is fixed-width uppercase ASCII, zero padded. Anything a title can
    contain and the field cannot — accents, CJK, punctuation — is dropped
    rather than truncated at the first one, so "Café Racer 2" still reads as
    CAFE RACER 2 instead of stopping at CAF."""
    try:
        import unicodedata
        name = unicodedata.normalize("NFKD", name)
    except Exception:
        pass
    out = []
    for ch in name.upper():
        if ch.isalnum() and ord(ch) < 128:
            out.append(ch)
        elif ch in " -_" and out and out[-1] != " ":
            out.append(" ")
    return "".join(out).strip()[:12].encode("ascii", "ignore")


def _gbafix(path, name=""):
    """Write the GBA cartridge header's boot logo, game title and checksum.

    The logo goes at 0x04..0x9F, the title at 0xA0..0xAB and the complement
    checksum at 0xBD (over 0xA0..0xBC). The logo and the checksum are required
    for the ROM to start on a real Game Boy Advance or a flashcart; an
    emulator's HLE BIOS skips straight to the cartridge and would run without
    either. The TITLE is what a flashcart menu lists the game under — left
    unwritten it stayed all zeros, so every game a person exported appeared in
    that menu as a blank row, indistinguishable from every other one they had
    made. It must be written BEFORE the checksum, which covers it."""
    with open(path, "rb") as fh:
        d = bytearray(fh.read())
    if len(d) < 0xC0:
        d += bytes(0xC0 - len(d))
    d[0x04:0xA0] = NINTENDO_LOGO
    title = _rom_title(name)
    d[0xA0:0xAC] = title + bytes(12 - len(title))
    s = 0
    for i in range(0xA0, 0xBD):
        s = (s + d[i]) & 0xFF
    d[0xBD] = (-(0x19 + s)) & 0xFF
    with open(path, "wb") as fh:
        fh.write(d)
