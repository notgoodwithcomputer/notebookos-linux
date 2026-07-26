#!/usr/bin/env python3
"""
gbabuild — turn a Notebook OS GBA IDE game model into a real .gba ROM.

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
import re
import json
import shutil
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


def _int(v, default=0):
    try:
        if isinstance(v, bool):
            return int(v)
        if isinstance(v, (int, float)):
            return int(v)
        return int(str(v).strip())
    except (ValueError, TypeError):
        return default


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
            out.append("\\x%02x" % (ord(ch) & 0xFF))
    out.append('"')
    return "".join(out)


class BuildError(Exception):
    pass


class GmlError(Exception):
    """A mistake in a user's GML. `line` is the 1-based line it was found on
    (0 when the position is unknown) so the IDE can point at it."""

    def __init__(self, message, line=0):
        super().__init__(message)
        self.line = line


# ---------------------------------------------------------------- GML compiler
GML_BUILTIN_VARS = {"x", "y", "hspeed", "vspeed", "image_index", "image_speed",
                    "grav"}
GML_GLOBALS = {"score": "nb_score", "lives": "nb_lives", "health": "nb_health"}
GML_KEYS = {"vk_left": "KEY_LEFT", "vk_right": "KEY_RIGHT", "vk_up": "KEY_UP",
            "vk_down": "KEY_DOWN", "vk_a": "KEY_A", "vk_b": "KEY_B",
            "vk_start": "KEY_START", "vk_select": "KEY_SELECT", "vk_l": "KEY_L",
            "vk_r": "KEY_R"}
GML_KEYWORDS = {"if", "else", "while", "repeat", "exit", "var", "true", "false",
                "return", "then", "begin", "end"}


class _Gml:
    """A small recursive-descent compiler for a useful GML subset -> C against
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
            raise GmlError("there is a %r here, which means nothing in code" % c,
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
            raise GmlError("the code stops in the middle of something",
                           self._line())
        t = self.toks[self.pos]; self.pos += 1; return t

    def _eat(self, val):
        t = self._peek()
        if not t or t[1] != val:
            raise GmlError("expected %s here, found %s"
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
                rhs = self._expr()
                if self._is(";"):
                    self._next()
                return ["%s%s %s %s;" % (pad, target, op, rhs)]
        except GmlError:
            pass
        self.pos = save
        expr = self._expr()
        if self._is(";"):
            self._next()
        return ["%s%s;" % (pad, expr)]

    def _lvalue(self):
        t = self._peek()
        if not t or t[0] != "id" or t[1] in GML_KEYWORDS:
            raise GmlError("this is not something you can assign to",
                           self._line())
        nxt = self._peek(1)
        if nxt and nxt[1] == "(":
            raise GmlError("you cannot assign to the result of %s()" % t[1],
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
            raise GmlError("the code stops in the middle of something",
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
        raise GmlError("%s does not belong here" % (t[1],), t[2])

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
            raise GmlError("there is no function called %s" % name, line)
        argc, tmpl = spec
        if len(args) != argc:
            raise GmlError("%s needs %d value%s inside its brackets, not %d"
                           % (name, argc, "" if argc == 1 else "s", len(args)),
                           line)
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
                raise GmlError("global. must be followed by a name",
                               self._line())
            self._next()
            slot = self.globals.get(field[1])
            if slot is None:
                raise GmlError("global.%s is never set anywhere; set it once "
                               "before you read it" % field[1], self._line())
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
        if name in GML_KEYS:
            return GML_KEYS[name]
        if name in GML_BUILTIN_VARS:
            return "self->%s" % name
        if name in GML_GLOBALS:
            return GML_GLOBALS[name]
        if name in self.vars:
            return "self->var[%d]" % self.vars[name]
        raise GmlError("%s is not a word this code knows; check the spelling"
                       % name, self._line())

    def _arr_ref(self, name, idx):
        if name == "alarm":
            return "self->alarm[%s]" % idx
        raise GmlError("unknown array %s" % name)


# ---------------------------------------------------------------- codegen
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
        # Mistakes found in the model while generating. A bad line of GML used
        # to become a silent C comment: the ROM built, the code did nothing, and
        # the author was never told. Collect them here so the IDE can show them.
        self.problems = []
        self._where = ""    # "object · event" being emitted, for problem text
        self._obj_id = ""
        self.global_ix = self._collect_globals()   # global.* name -> slot

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
            cols = cols[:15]
            chosen = None
            for bi, bank in enumerate(banks):
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
            self._spr_bank[si] = chosen
            cmap = {TRANSPARENT & 0x7FFF: 0}
            for c in cols:
                cmap[c] = banks[chosen].get(c, 1)
            self._spr_cmap[si] = cmap
        pal = [0] * 256
        for bi, bank in enumerate(banks):
            for c, idx in bank.items():
                pal[bi * 16 + idx] = c
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
        tiles = [0] * 16                    # tile 0 = blank
        for ts in self.tilesets:
            for tile in ts.get("tiles") or []:
                px = (list(tile) + [TRANSPARENT] * 64)[:64]
                idx = []
                for p in px:
                    c = _int(p, TRANSPARENT) & 0x7FFF
                    if c not in cmap:
                        if nxt < 16:
                            cmap[c] = nxt
                            pal[nxt] = c
                            nxt += 1
                        else:
                            cmap[c] = 1
                    idx.append(cmap[c])
                for row in range(8):
                    for half in range(2):
                        v = 0
                        for k in range(4):
                            v |= (idx[row * 8 + half * 4 + k] & 0xF) << (k * 4)
                        tiles.append(v)
        self._bg_cmap = cmap
        self.w("const u16 nb_bg_palette[16] = { %s };"
               % ", ".join("0x%04X" % (c & 0x7FFF) for c in pal))
        self.w("const u16 nb_bg_tiles[] = { %s };"
               % (", ".join("0x%04X" % v for v in tiles) or "0x0000"))
        self.w("const int nb_bg_tile_count = %d;" % (len(tiles) // 16))
        self.w("")

    # ---- objects / events / actions ----
    def _collect_vars(self, obj):
        """Ordered unique user-variable names referenced in an object."""
        names = []

        def scan(actions):
            for a in actions or []:
                if not isinstance(a, dict):
                    continue
                k = a.get("kind")
                if k in ("set_var", "add_var", "if_var"):
                    v = str(a.get("var", "")).strip()
                    if v and v not in names:
                        names.append(v)
                elif k == "execute_code":
                    for v in self._gml_user_vars(a.get("code", "")):
                        if v not in names:
                            names.append(v)
                scan(a.get("children"))

        for ev in obj.get("events") or []:
            scan(ev.get("actions"))
        return {n: i for i, n in enumerate(names[:12])}

    def _collect_globals(self):
        """Project-wide global.* names -> slot (persistent, saved to SRAM)."""
        names = []

        def scan(acts):
            for a in acts or []:
                if not isinstance(a, dict):
                    continue
                if a.get("kind") == "execute_code":
                    for nm in self._gml_globals(a.get("code", "")):
                        if nm not in names:
                            names.append(nm)
                scan(a.get("children"))
        for o in self.objects:
            for ev in o.get("events") or []:
                scan(ev.get("actions"))
        return {n: i for i, n in enumerate(names[:32])}

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

    def _gml_user_vars(self, code):
        """Identifiers in GML that are user variables — not resources, built-ins,
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
            if (name in GML_KEYWORDS or name in GML_BUILTIN_VARS
                    or name in GML_GLOBALS or name in GML_KEYS
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
        if s in GML_GLOBALS:
            return GML_GLOBALS[s]
        return str(_int(s, 0))

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
        elif k == "play_sound":
            si = self.snd_ix.get(a.get("sound"))
            if si is not None:
                L.append("%srt_play_sound(%d);" % (pad, si))
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
        elif k == "set_image_speed":
            L.append("%sself->image_speed = %s;" %
                     (pad, self._val(a.get("value"), vars_map)))
        elif k == "destroy_object":
            oi = self.obj_ix.get(a.get("object"))
            if oi is not None:
                L.append("%srt_destroy_object(%d);" % (pad, oi))
        elif k in ("set_score", "set_lives", "set_health"):
            g = {"set_score": "nb_score", "set_lives": "nb_lives",
                 "set_health": "nb_health"}[k]
            L.append("%s%s = %s;" % (pad, g, self._val(a.get("value"), vars_map)))
        elif k in ("add_score", "add_lives", "add_health"):
            g = {"add_score": "nb_score", "add_lives": "nb_lives",
                 "add_health": "nb_health"}[k]
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
        elif k == "clear_text":
            L.append("%srt_clear_text();" % pad)
        elif k == "save_game":
            L.append("%srt_game_save();" % pad)
        elif k == "load_game":
            L.append("%srt_game_load();" % pad)
        elif k == "execute_code":
            try:
                L += _Gml(self, vars_map, self.global_ix).compile(
                    a.get("code", ""), ind)
            except GmlError as e:
                L.append("%s/* GML error: %s */" %
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
        # Remember where we are so a GML mistake is reported against the object
        # and event the author can actually click on.
        self._where = "%s · %s" % (self._obj_id or "?", self._event_name(ev))
        L = []
        for a in (ev or {}).get("actions") or []:
            L += self._emit_action(a, vars_map, 1)
        self._where = ""
        return L

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

            def emit_fn(name, events):
                self.w("static void %s(Instance* self) {" % name)
                self.w("    (void)self;")
                for e in events:
                    for ln in self._emit_event_body(e, vars_map):
                        self.w(ln)
                self.w("}")

            if create:
                emit_fn("%s_create" % cid, create)
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
                        continue
                    self.w("    if (rt_meeting(self, %d)) {" % oi)
                    for ln in self._emit_event_body(e, vars_map):
                        self.w("    " + ln)
                    self.w("    }")
                self.w("}")
            o["_cid"] = cid
            o["_has_create"] = bool(create)
            o["_has_step"] = has_step
            o["_has_destroy"] = bool(destroys)
        self.w("")
        # object table: { sprite, visible, solid, create, step, draw, destroy }
        self.w("const nb_Object nb_objects[] = {")
        for o in self.objects:
            cid = o["_cid"]
            spr = self.spr_ix.get(o.get("sprite"), -1)
            vis = 0 if o.get("visible") is False else 1
            solid = 1 if o.get("solid") else 0
            cfn = "%s_create" % cid if o["_has_create"] else "0"
            sfn = "%s_step" % cid if o["_has_step"] else "0"
            dfn = "%s_destroy" % cid if o["_has_destroy"] else "0"
            self.w("    { %d, %d, %d, %s, %s, 0, %s }," %
                   (spr, vis, solid, cfn, sfn, dfn))
        if not self.objects:
            self.w("    { -1, 1, 0, 0, 0, 0, 0 },")
        self.w("};")
        self.w("const int nb_object_count = %d;" % len(self.objects))
        self.w("")

    # ---- rooms ----
    def gen_rooms(self):
        has_tiles = {}
        for i, r in enumerate(self.rooms):
            insts = r.get("instances") or []
            self.w("static const nb_InstanceDef room_%d_insts[] = {" % i)
            for it in insts:
                oi = self.obj_ix.get(it.get("object"))
                if oi is None:
                    continue
                self.w("    { %d, %d, %d }," %
                       (oi, _int(it.get("x")), _int(it.get("y"))))
            self.w("    { -1, 0, 0 },")
            self.w("};")
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
            self.w("    { %d, %d, 0x%04X, %d, %d, room_%d_insts, %s }," %
                   (w, h, bg, speed, n, i, tiles_ref))
        if not self.rooms:
            self.w("    { 240, 160, 0x0000, 60, 0, 0, 0 },")
        self.w("};")
        self.w("const int nb_room_count = %d;" % len(self.rooms))
        start = self.room_ix.get(self.m.get("start_room"), 0)
        self.w("const int nb_start_room = %d;" % (start if self.rooms else 0))
        self.w("")

    # ---- sounds ----
    def gen_sounds(self):
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
        self.w("")
        self.w("const nb_Sound nb_sounds[] = {")
        for i, s in enumerate(self.sounds):
            tempo = max(1, min(60, _int(s.get("tempo"), 6)))
            loop = 1 if s.get("loop") else 0
            n = max(len(s.get("lead") or []), len(s.get("bass") or []), 1)
            self.w("    { %d, %d, %d, snd_lead_%d, snd_bass_%d },"
                   % (tempo, loop, n, i, i))
        if not self.sounds:
            self.w("    { 6, 0, 0, 0, 0 },")
        self.w("};")
        self.w("const int nb_sound_count = %d;" % len(self.sounds))
        self.w("")

    def generate(self):
        self.w("/* auto-generated by the Notebook OS GBA IDE — do not edit */")
        self.w('#include "runtime.h"')
        self.w("")
        self.gen_sprites()
        self.gen_bg()
        self.gen_sounds()
        self.gen_objects()
        self.gen_rooms()
        return "\n".join(self.out) + "\n"


def generate_c(model):
    return _Gen(model).generate()


def check_project(model):
    """Every mistake in `model` that the compiler would otherwise swallow, as a
    list of plain sentences ready to show the author.

    A line of GML the compiler cannot understand used to become a C comment: the
    ROM built, that code silently did nothing, and nobody was told. This runs the
    same generation pass and hands back what it found, so the IDE can say
    "obj_player · Step · line 3 — hspee is not a word this code knows" before it
    exports something that will not work. Never raises."""
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


def build_rom(model, outdir, runtime_dir=RUNTIME_DIR, toolchain_dir=TOOLCHAIN_DIR):
    """Compile `model` into <outdir>/game.gba. Returns (ok, gba_path, log)."""
    gcc = find_gcc(toolchain_dir)
    if not gcc:
        return False, None, "The GBA toolchain (arm-none-eabi-gcc) isn't installed."
    tdir = os.path.dirname(gcc)
    objcopy = os.path.join(tdir, "arm-none-eabi-objcopy")
    problems = []
    try:
        os.makedirs(outdir, exist_ok=True)
        gen = _Gen(model)
        source = gen.generate()
        problems = list(gen.problems)
        with open(os.path.join(outdir, "game_data.c"), "w") as fh:
            fh.write(source)
    except Exception as e:
        return False, None, "Could not write generated source: %s" % e

    head = ""
    if problems:
        head = ("This game has %d problem%s the compiler could not use:\n  %s\n\n"
                % (len(problems), "" if len(problems) == 1 else "s",
                   "\n  ".join(problems)))
    elf = os.path.join(outdir, "game.elf")
    gba = os.path.join(outdir, "game.gba")
    cmd = [
        gcc, "-mcpu=arm7tdmi", "-mthumb-interwork", "-ffreestanding",
        "-nostdlib", "-O2", "-Wall",
        "-I", runtime_dir,
        "-T", os.path.join(runtime_dir, "gba.ld"),
        os.path.join(runtime_dir, "crt0.s"),
        os.path.join(runtime_dir, "runtime.c"),
        os.path.join(outdir, "game_data.c"),
        # ARM7TDMI has no hardware divide; the compiler emits calls to libgcc's
        # __aeabi_*div helpers, so link libgcc even though we're freestanding.
        "-lgcc",
        "-o", elf,
    ]
    log = head + " ".join(cmd) + "\n"
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        log += r.stdout + r.stderr
        if r.returncode != 0:
            return False, None, log
    except Exception as e:
        return False, None, log + "\ncompile failed: %s" % e
    try:
        r = subprocess.run([objcopy, "-O", "binary", elf, gba],
                           capture_output=True, text=True, timeout=60)
        log += r.stdout + r.stderr
        if r.returncode != 0:
            return False, None, log
        _gbafix(gba)
    except Exception as e:
        return False, None, log + "\nlink/fix failed: %s" % e
    return True, gba, log + "\nBuilt %s (%d bytes)\n" % (gba, os.path.getsize(gba))


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


def _gbafix(path):
    """Write the GBA cartridge header's boot logo and complement checksum.

    The logo goes at 0x04..0x9F and the checksum at 0xBD (over 0xA0..0xBC).
    Both are required for the ROM to start on a real Game Boy Advance or a
    flashcart; an emulator's HLE BIOS skips straight to the cartridge and would
    run without either."""
    with open(path, "rb") as fh:
        d = bytearray(fh.read())
    if len(d) < 0xC0:
        d += bytes(0xC0 - len(d))
    d[0x04:0xA0] = NINTENDO_LOGO
    s = 0
    for i in range(0xA0, 0xBD):
        s = (s + d[i]) & 0xFF
    d[0xBD] = (-(0x19 + s)) & 0xFF
    with open(path, "wb") as fh:
        fh.write(d)
