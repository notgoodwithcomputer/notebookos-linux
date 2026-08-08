#!/usr/bin/env python3
"""
Calculator — the Notebook OS scientific calculator (native GTK).

A single paper card centred on the desk: a right-aligned display with a faint
running-history line, and a 6-column keypad of numbers, operators, and scientific
functions. Computes for real (trig in degrees or radians, powers, roots,
logs, factorials, constants). Opens empty, showing 0.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, Pango  # noqa: E402

import math
import ast
import re
import json
import os
import random

import nbapp
import nbicons  # pictographic backspace glyph on the ⌫ key
from nbi18n import _t  # noqa: E402

# Angle mode (degrees / radians) is the one preference worth remembering across
# launches, so someone who works in radians need not re-toggle every time. It
# lives in this app's private JSON file under the shared per-app config dir
# (NB_HOME, falling back to the user's home dir as elsewhere). The computation
# itself is never persisted — the calculator still opens empty, showing 0.
HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
CFG_DIR = os.path.join(HOME, ".config", "notebook")
STATE_FILE = os.path.join(CFG_DIR, "calculator.json")

# What the display says when "=" cannot be answered. The old display read
# "Error", which tells someone who has just mistyped a bracket nothing at all
# about which mistake they made or what to do about it. Four causes cover
# every failure this calculator can have, and each is one short sentence so it
# still fits the display without ellipsizing. Kept as constants because they
# are set on a worker path and translated at paint time.
_WHY_ZERO = "Cannot divide by zero"
_WHY_TOOBIG = "The answer is too big to show"
_WHY_NOANSWER = "There is no answer to that"
_WHY_UNREADABLE = "That is not a calculation this can work out"

CATALOG = {
    "Trigonometry": (("sin(", "sin("), ("cos(", "cos("), ("tan(", "tan("),
                     ("sin^-1(", "asin("), ("cos^-1(", "acos("),
                     ("tan^-1(", "atan("), ("sinh(", "sinh("),
                     ("cosh(", "cosh("), ("tanh(", "tanh(")),
    "Logarithms": (("ln(", "ln("), ("log(", "log("), ("log2(", "log2("),
                   ("e^x", "exp("), ("10^x", "pow10(")),
    "Number": (("sqrt(", "sqrt("), ("nthRoot(", "root("), ("abs(", "abs("),
               ("floor(", "floor("), ("ceil(", "ceil("), ("round(", "round("),
               ("frac(", "frac("), ("int(", "int(")),
    "Probability": (("factorial(", "fact("), ("nCr(", "nCr("),
                    ("nPr(", "nPr("), ("random", "random()")),
}


def format_number(value, fix=None):
    """Format display, trace and table numbers through one mode."""
    if fix is not None:
        return ("%%.%df" % max(0, min(9, int(fix)))) % float(value)
    return "%.12g" % float(value)


def graph_to_pixel(x, y, window, width, height):
    return ((x - window["xmin"]) * width / (window["xmax"] - window["xmin"]),
            (window["ymax"] - y) * height / (window["ymax"] - window["ymin"]))


def pixel_to_graph(px, py, window, width, height):
    return (window["xmin"] + px * (window["xmax"] - window["xmin"]) / width,
            window["ymax"] - py * (window["ymax"] - window["ymin"]) / height)


def sample_segments(fn, xmin, xmax, samples=401):
    """Return finite polyline segments, splitting poles instead of walls."""
    points, segments, previous = [], [], None
    dx = (xmax - xmin) / max(1, samples - 1)
    for i in range(samples):
        x = xmin + i * dx
        try:
            y = float(fn(x))
            good = math.isfinite(y)
        except Exception:
            good, y = False, float("nan")
        # A sign flip with huge endpoints is the characteristic sampled pole.
        pole = (previous is not None and good and previous[1] * y < 0 and
                max(abs(previous[1]), abs(y)) > 20.0)
        if not good or pole:
            if points:
                segments.append(points)
            points = []
        if good:
            points.append((x, y))
            previous = (x, y)
        else:
            previous = None
    if points:
        segments.append(points)
    return segments


def tape_rows(tape, tape_results):
    """The (expression, result) rows the display tape paints, oldest first.

    The two lists are kept in lockstep (a None result marks an attempt that
    failed and paints nothing), but the pairing is still clamped to the
    shorter list, so a desynced pair — an older or hand-edited state file —
    paints what it can instead of crashing the repaint with an IndexError."""
    rows = []
    for i in range(min(len(tape), len(tape_results))):
        result = tape_results[i]
        if result is not None:
            rows.append((tape[i], result))
    return rows


def tape_window(tape, tape_results, offset=None, count=3):
    """Return a bounded slice of the rows painted in the compact tape area.

    ``offset`` is the first visible row; ``None`` follows the newest rows.  Both
    ends are clamped after result-less attempts have been removed, so empty,
    short, recalled and just-cleared tapes all produce a safe slice.
    """
    rows = tape_rows(tape, tape_results)
    count = max(0, int(count))
    last_offset = max(0, len(rows) - count)
    if offset is None:
        offset = last_offset
    offset = min(max(0, int(offset)), last_offset)
    return rows[offset:min(offset + count, len(rows))]


# How many worked-out calculations the tape keeps (mirrored by the class as
# _TAPE_MAX; sanitize_state trims a loaded file to the same window).
TAPE_MAX = 30

_WINDOW_DEFAULT = {"xmin": -10.0, "xmax": 10.0, "ymin": -10.0, "ymax": 10.0,
                   "xscl": 1.0, "yscl": 1.0}


def _finite(value, fallback):
    """float(value) when it names a real, finite number; the fallback else."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return fallback
    return v if math.isfinite(v) else fallback


def sanitize_state(state):
    """Reduce whatever calculator.json held to fields every windowed/indexed
    render can use blindly: the tape pair-aligned and trimmed to its window,
    exactly four graph functions and enable flags, a window whose bounds are
    ordered and whose scales are positive, and numeric table/trace/format
    fields. A file from an older build (which stored results only for
    successful "="s) or a hand-edited one must degrade to something drawable,
    never to an IndexError or a KeyError inside a paint."""
    if not isinstance(state, dict):
        state = {}

    raw = state.get("tape")
    tape = [str(x) for x in raw] if isinstance(raw, list) else []
    raw = state.get("tape_results")
    results = ([None if x is None else str(x) for x in raw]
               if isinstance(raw, list) else [])
    # Older files hold one result per SUCCESS, not per attempt: pair those
    # from the end, the way that build painted them, and pad the front with
    # blanks. A longer results list (the desync this replaces) keeps its tail.
    if len(results) < len(tape):
        results = [None] * (len(tape) - len(results)) + results
    elif len(results) > len(tape):
        results = results[len(results) - len(tape):]
    tape, results = tape[-TAPE_MAX:], results[-TAPE_MAX:]

    variables = {}
    raw = state.get("variables")
    if isinstance(raw, dict):
        for k, v in raw.items():
            if (isinstance(k, str) and re.match(r"^[A-Z]$", k)
                    and isinstance(v, (int, float))
                    and not isinstance(v, bool) and math.isfinite(float(v))):
                variables[k] = float(v)

    ans = state.get("ans", 0)
    if (isinstance(ans, bool) or not isinstance(ans, (int, float))
            or not math.isfinite(float(ans))):
        ans = 0

    fix = state.get("fix")
    if isinstance(fix, bool) or not isinstance(fix, int) or not 0 <= fix <= 9:
        fix = None

    raw = state.get("ys")
    raw = raw if isinstance(raw, list) else ["sin(X)", "", "", ""]
    ys = ["" if x is None else str(x) for x in raw[:4]]
    ys += [""] * (4 - len(ys))
    raw = state.get("y_enabled")
    raw = raw if isinstance(raw, list) else [True, False, False, False]
    y_enabled = [bool(x) for x in raw[:4]]
    y_enabled += [False] * (4 - len(y_enabled))

    window = dict(_WINDOW_DEFAULT)
    raw = state.get("window")
    if isinstance(raw, dict):
        for key in window:
            window[key] = _finite(raw.get(key), window[key])
    if window["xmin"] >= window["xmax"]:
        window["xmin"], window["xmax"] = _WINDOW_DEFAULT["xmin"], _WINDOW_DEFAULT["xmax"]
    if window["ymin"] >= window["ymax"]:
        window["ymin"], window["ymax"] = _WINDOW_DEFAULT["ymin"], _WINDOW_DEFAULT["ymax"]
    if window["xscl"] <= 0:
        window["xscl"] = _WINDOW_DEFAULT["xscl"]
    if window["yscl"] <= 0:
        window["yscl"] = _WINDOW_DEFAULT["yscl"]

    deg = state.get("deg")
    return {
        "tape": tape, "tape_results": results, "variables": variables,
        "ans": ans, "fix": fix, "ys": ys, "y_enabled": y_enabled,
        "window": window,
        "tbl_start": _finite(state.get("tbl_start"), 0.0),
        "tbl_step": _finite(state.get("tbl_step"), 1.0),
        "trace_x": _finite(state.get("trace_x"), 0.0),
        "deg": deg if isinstance(deg, bool) else True,
    }


def table_values(fn, start, step, rows=10):
    out = []
    for i in range(rows):
        x = start + i * step
        try:
            y = fn(x)
            y = y if math.isfinite(float(y)) else None
        except Exception:
            y = None
        out.append((x, y))
    return out


# key defs: (label, action, value, type)
#   type -> num / op / eq / clear / fn
KEYS = [
    ("STO→", "store", None, "fn"), ("MATH", "catalog", None, "fn"),
    ("2nd", "second", None, "fn"), ("DEG", "deg", None, "fn"),
    ("sin", "app", "sin(", "fn"), ("cos", "app", "cos(", "fn"),
    ("tan", "app", "tan(", "fn"), ("AC", "ac", None, "clear"),

    ("xʸ", "app", "^", "fn"), ("log", "app", "log(", "fn"),
    ("ln", "app", "ln(", "fn"), ("(", "app", "(", "fn"),
    (")", "app", ")", "fn"), ("⌫", "back", None, "clear"),  # rendered as a nbicons pictographic glyph (see _keypad)

    ("√", "app", "√(", "fn"), ("π", "app", "π", "fn"),
    ("7", "app", "7", "num"), ("8", "app", "8", "num"),
    ("9", "app", "9", "num"), ("÷", "app", "÷", "op"),

    ("x²", "app", "^2", "fn"), ("e", "app", "e", "fn"),
    ("4", "app", "4", "num"), ("5", "app", "5", "num"),
    ("6", "app", "6", "num"), ("×", "app", "×", "op"),

    ("1/x", "inv", None, "fn"), ("x!", "app", "!", "fn"),
    ("1", "app", "1", "num"), ("2", "app", "2", "num"),
    ("3", "app", "3", "num"), ("−", "app", "−", "op"),

    ("±", "neg", None, "fn"), ("%", "app", "%", "fn"),
    ("0", "app", "0", "num"), (".", "app", ".", "num"),
    ("=", "eq", None, "eq"), ("+", "app", "+", "op"),
]

ALT_VALUE = {"sin(": "asin(", "cos(": "acos(", "tan(": "atan("}

# The scientific keys whose FACE flips to an inverse (arc-) function when the
# 2nd toggle is on. Their inverse face is drawn as a superscript "-1" via Pango
# markup (see _sync_dynamic_keys) built from ORDINARY base-font characters, so
# it never depends on the exotic superscript codepoints (U+207B ⁻, U+00B9 ¹)
# that on this image only DejaVu carries — no "tofu" on a bare panel font.
INV_KEYS = ("sin", "cos", "tan")

# Two power keys carry a superscript that would otherwise need an exotic
# codepoint on the face — xʸ = "x" + U+02B8 (MODIFIER LETTER SMALL Y), x² = "x"
# + U+00B2 — either of which can render as blank "tofu" when the panel's chosen
# font lacks the glyph. Guest Pango is 1.50, so we draw both as <sup> markup
# built from plain base-font characters (x, y, 2), keyed here by each key's
# ASCII value ("^", "^2") so no exotic character appears in this source at all.
# Guaranteed to exist in Nimbus Sans/Liberation/DejaVu, and the two power keys
# then look visually identical.
SUP_MARKUP = {"^": "x<sup>y</sup>", "^2": "x<sup>2</sup>"}

# Plain-language hover labels for the scientific keys, so a novice can learn
# what each does. Digits and the basic + − × ÷ = . keys are self-evident and
# deliberately carry no tooltip. sin/cos/tan and DEG get live tooltips in
# _refresh (they change with the 2nd toggle / angle mode).
TOOLTIPS = {
    "2nd": "Inverse trigonometric functions",
    "sin": "Sine", "cos": "Cosine", "tan": "Tangent",
    "AC": "Clear all",
    "xʸ": "Power (x raised to y)",
    "log": "Base-10 logarithm", "ln": "Natural logarithm",
    "(": "Open parenthesis", ")": "Close parenthesis",
    "√": "Square root", "π": "Pi (3.14159…)",
    "x²": "Square", "e": "Euler's number (2.71828…)",
    "1/x": "Reciprocal (1 divided by x)", "x!": "Factorial",
    "±": "Negate (change sign)",
    "%": "Percent — 200+10% is 220; 50% on its own is 0.5",
}
ALT_TOOLTIP = {"sin": "Inverse sine", "cos": "Inverse cosine",
               "tan": "Inverse tangent"}


def _guarded_pow(base, exp):
    # Bounded ** for eval(): reject powers whose result would be astronomically
    # large BEFORE Python builds a multi-million-digit integer that hangs/OOMs
    # the app (e.g. 9^9^9 == 9**(9**9)). The result has ~|exp|*log10(|base|)
    # digits; anything past a sane display size raises so evaluate() degrades to
    # "Error". Ordinary powers (2^10, 2^1000, roots, |base|<=1, fractional
    # exponents) pass straight through, preserving valid-expression behavior.
    try:
        b = abs(base)
        if b > 1 and abs(exp) * math.log10(b) > 10000:
            raise OverflowError("power too large")
    except (TypeError, ValueError):
        pass
    return base ** exp


_PCT_REL = re.compile(r"([+\-])(\d+(?:\.\d+)?)%")
_PCT_ANY = re.compile(r"(\d+(?:\.\d+)?)%")


def _operand_start(s, i):
    """Index where the operand ending at `i` begins: the start of the current
    parenthesised level. Walking back over balanced groups is what keeps
    "2*(3+10%)" from being rewritten with the "(" of an outer group inside it."""
    depth = 0
    while i > 0:
        c = s[i - 1]
        if c == ")":
            depth += 1
        elif c == "(":
            if depth == 0:
                return i
            depth -= 1
        i -= 1
    return 0


def _expand_percent(s):
    """Rewrite `%` the way a calculator on a desk means it.

    A percentage is nearly always OF something: a tip, VAT, a discount. Reading
    "%" as nothing but "divide by 100" made 200+10% come back 200.1, where every
    consumer calculator — and everyone pressing the key — says 220. So after
    "+" or "-", "N%" means N percent OF the running left-hand value:

        200+10%   -> 200+(200)*(10/100)   = 220
        200-10%   -> 200-(200)*(10/100)   = 180
        100+5%+5% -> 110.25, each 5% taken of the total so far

    Everywhere else "N%" keeps its plain meaning of N/100, which is what makes
    "50%", "200*10%" (= 20) and "10%*3" read correctly. Leftmost first, so a
    chain compounds in the order it was typed."""
    pos = 0
    guard = 0
    while guard < 64:
        guard += 1
        m = _PCT_REL.search(s, pos)
        if m is None:
            break
        start = _operand_start(s, m.start())
        left = s[start:m.start()]
        if not left.strip():
            # "+10%" with nothing in front of it (a leading sign) is not a
            # percentage OF anything — leave it to the plain rule below.
            pos = m.end()
            continue
        rep = "%s%s(%s)*(%s/100)" % (left, m.group(1), left, m.group(2))
        s = s[:start] + rep + s[m.end():]
        pos = start + len(rep)
    return _PCT_ANY.sub(r"(\1/100)", s)


def _postfix_fact(s):
    """Rewrite a postfix `!` into fact(...), taking whatever operand precedes it.

    The x! key writes a bare "!", which Python cannot parse, so the operand has
    to be found and wrapped here. The old rule only understood a run of digits,
    so "(2+3)!" — an ordinary thing to press — came back "Error" from a key
    labelled Factorial, as did "sqrt(9)!". Walk back over a balanced group (with
    any function name in front of it), a number, or a constant instead, so every
    operand the keypad can produce works. Returns None when there is no operand
    at all ("!" on its own), which the caller reports as Error."""
    guard = 0
    while "!" in s:
        guard += 1
        if guard > 64:                     # pathological input; refuse
            return None
        i = s.index("!")
        j = i - 1
        if j < 0:
            return None
        if s[j] == ")":
            depth = 0
            while j >= 0:
                if s[j] == ")":
                    depth += 1
                elif s[j] == "(":
                    depth -= 1
                    if depth == 0:
                        break
                j -= 1
            if j < 0:
                return None                # unbalanced
            while j > 0 and (s[j - 1].isalpha() or s[j - 1] == "_"):
                j -= 1                     # the function name owns its group
        elif s[j].isdigit() or s[j] == ".":
            while j > 0 and (s[j - 1].isdigit() or s[j - 1] == "."):
                j -= 1
        elif s[j].isalpha() or s[j] == "_":
            while j > 0 and (s[j - 1].isalpha() or s[j - 1] == "_"):
                j -= 1
        else:
            return None                    # nothing to take the factorial of
        s = s[:j] + "fact(" + s[j:i] + ")" + s[i + 1:]
    return s


# Digits an exact integer result may carry before it is shown as a magnitude
# instead. Measured against the display: the card is 640px wide and the result
# is set at 52px, so about twenty characters fit before the label starts
# ellipsizing. 20! (19 digits) stays exact; 200! becomes 7.88657867365e+374.
_MAX_DIGITS = 20


def _sci(digits):
    """Format a very long exact integer (as its decimal string) in scientific
    notation, rounded to 12 significant figures.

    An exact 375-digit answer is not a readable one on a single line: the
    display ellipsizes it and shows only its LAST digits, which say nothing at
    all about how big the number is. float() cannot help — anything past ~1e308
    overflows — so the mantissa is rounded out of the digit string itself."""
    neg = digits.startswith("-")
    d = digits.lstrip("-")
    exp = len(d) - 1
    head = d[:13].ljust(13, "0")
    n = (int(head) + 5) // 10              # round half up at the 12th figure
    if n >= 10 ** 12:                      # the rounding carried into a new digit
        n //= 10
        exp += 1
    m = str(n).rstrip("0") or "1"
    mant = m[0] + ("." + m[1:] if len(m) > 1 else "")
    return "%s%se+%d" % ("-" if neg else "", mant, exp)


class _PowGuard(ast.NodeTransformer):
    # Rewrite every  a ** b  into  _pow(a, b)  so nested/oversized powers are
    # bounded at runtime. A purely static exponent cap can't see that 9**9**9
    # actually means 9**(9**9) (each literal exponent is just 9).
    def visit_BinOp(self, node):
        self.generic_visit(node)
        if isinstance(node.op, ast.Pow):
            return ast.copy_location(
                ast.Call(func=ast.Name(id="_pow", ctx=ast.Load()),
                         args=[node.left, node.right], keywords=[]),
                node)
        return node


class Calculator(nbapp.AppWindow):
    app_name = "Calculator"
    menus = ("Edit", "View")

    def __init__(self):
        super().__init__()
        self._install_css()

        self.expr = ""
        self.history = ""
        # Everything that has been worked out this session, oldest first. Up /
        # Down (and a click on the history line) walk it back into the display:
        # a long sum with one wrong digit is then one key away instead of being
        # typed out again from the beginning, which is the single most common
        # thing anyone wants back from a calculator. Not persisted — the
        # calculator still opens empty, showing 0.
        state = sanitize_state(self._load_state())
        self.tape = state["tape"]
        self.tape_results = state["tape_results"]
        self.variables = state["variables"]
        self.ans = state["ans"]
        self.fix = state["fix"]
        self.ys = state["ys"]
        self.y_enabled = state["y_enabled"]
        self.window = state["window"]
        self.tbl_start = state["tbl_start"]
        self.tbl_step = state["tbl_step"]
        self.trace_x = state["trace_x"]
        self.trace_curve = 0
        self._tape_i = None       # position while walking it (None = not)
        self._tape_draft = ""     # what was on the display before walking
        self.deg = state["deg"]
        self.just_evaled = False
        self.second = False
        self.error = False   # last "=" failed; the display says why, in red
        self._err_why = None  # which _WHY_* sentence to show while error
        self._buttons = []   # (keydef, button, label_widget)

        # Only a HANDFUL of keys ever change their face/tooltip (the DEG/RAD
        # key, the sin/cos/tan inverse faces, the 2nd active state); the digit
        # and operator keys never do. We collect just those dynamic widgets at
        # build time and touch nothing else on a keypress, so typing a digit
        # forces no keypad relayout on this CPU-only software renderer. The
        # caches below skip a set_text/set_markup/set_tooltip when the value is
        # unchanged — a redundant set still triggers a relayout + repaint.
        self._deg_btn = None       # (button, label_widget) for the DEG/RAD key
        self._inv_btns = []        # [(label, button, label_widget)] sin/cos/tan
        self._second_btn = None    # the 2nd toggle button (carries .active)
        self._face_cache = {}      # label_widget -> last applied ("t"/"m", text)
        self._tip_cache = {}       # button -> last applied tooltip text
        self._hist_txt = None      # last history-line text
        self._mode_txt = None      # last DEGREES/RADIANS text

        # background desk
        shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        nav.get_style_context().add_class("calcnav")
        self._views = {}
        for key, label in (("home", "Home"), ("graph", "Graph"), ("table", "Table")):
            button = Gtk.Button(label=_t(label))
            button.connect("clicked", lambda _b, k=key: self._switch_view(k))
            nav.pack_start(button, True, True, 0)
            self._views[key] = button
        shell.pack_start(nav, False, False, 0)
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        shell.pack_start(self.stack, True, True, 0)
        self.content.pack_start(shell, True, True, 0)

        stage = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        stage.get_style_context().add_class("calcstage")
        stage.set_hexpand(True)
        stage.set_vexpand(True)

        # Fit-and-scroll safety net: on a short panel where the card + desk
        # margin would exceed the viewport, the desk scrolls vertically rather
        # than clipping the keypad. When it fits (every supported panel), the
        # vexpanding stage fills the viewport and the card stays centred, so the
        # scrollbar never appears. Horizontal is pinned NEVER — the card width
        # is already capped to the screen above, so it always fits across.
        # The scroller + its viewport paint the OPAQUE desk tone (no rgba /
        # transparent window background — that would render solid black on the
        # no-compositor stack).
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.get_style_context().add_class("calcscroll")
        scroller.set_hexpand(True)
        scroller.set_vexpand(True)
        scroller.add(stage)
        self.stack.add_named(scroller, "home")

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.get_style_context().add_class("calccard")
        # Card width tracks the real panel, never a hardcoded 1920/1080 stack:
        # 640 on a normal desk, but capped to the live screen (minus the desk
        # margin) so the card can never overflow a small 1366x768 / 1280x800
        # panel. min 320 keeps the keypad usable on an unusually narrow screen.
        sw, _sh = nbapp.screen_size()
        card.set_size_request(max(320, min(640, sw - 96)), -1)
        card.set_halign(Gtk.Align.CENTER)
        card.set_valign(Gtk.Align.CENTER)
        stage.set_halign(Gtk.Align.CENTER)
        stage.set_valign(Gtk.Align.CENTER)
        stage.pack_start(card, False, False, 0)

        card.pack_start(self._display(), False, False, 0)
        card.pack_start(self._keypad(), False, False, 0)
        self.stack.add_named(self._graph_page(), "graph")
        self.stack.add_named(self._table_page(), "table")
        self._switch_view("home")

        self.connect("key-press-event", self._on_key_calc)
        self.connect("destroy", self._on_destroy)
        self._refresh()

    def _switch_view(self, name):
        self.current_view = name
        self.stack.set_visible_child_name(name)
        for key, button in self._views.items():
            ctx = button.get_style_context()
            (ctx.add_class if key == name else ctx.remove_class)("active")
        if name == "table":
            self._refresh_table()
        if name == "graph":
            self.graph.queue_draw()

    def _graph_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        page.get_style_context().add_class("graphpage")
        editor = Gtk.Grid(column_spacing=8, row_spacing=4)
        self.y_entries = []
        self.y_checks = []
        for i in range(4):
            check = Gtk.CheckButton(label="Y%d" % (i + 1))
            check.set_active(bool(self.y_enabled[i]))
            entry = Gtk.Entry()
            entry.set_text(self.ys[i])
            entry.connect("changed", self._on_y_changed, i)
            check.connect("toggled", self._on_y_toggle, i)
            editor.attach(check, 0, i, 1, 1)
            editor.attach(entry, 1, i, 1, 1)
            self.y_entries.append(entry)
            self.y_checks.append(check)
        page.pack_start(editor, False, False, 0)
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for label, fn in (("Window…", self._window_dialog),
                          ("Zoom Standard", lambda *_: self._zoom("standard")),
                          ("Zoom Fit", lambda *_: self._zoom("fit")),
                          ("Zoom In", lambda *_: self._zoom("in")),
                          ("Zoom Out", lambda *_: self._zoom("out"))):
            b = Gtk.Button(label=_t(label)); b.connect("clicked", fn)
            controls.pack_start(b, False, False, 0)
        page.pack_start(controls, False, False, 0)
        self.graph = Gtk.DrawingArea()
        self.graph.set_size_request(320, 240)
        self.graph.set_can_focus(True)
        self.graph.connect("draw", self._draw_graph)
        self.graph.connect("key-press-event", self._on_graph_key)
        page.pack_start(self.graph, True, True, 0)
        self.trace_label = Gtk.Label(label="", xalign=0)
        page.pack_start(self.trace_label, False, False, 0)
        return page

    def _table_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        page.get_style_context().add_class("tablepage")
        settings = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for label, value, attr in (("Table Start", self.tbl_start, "tbl_start"),
                                   ("Table Step", self.tbl_step, "tbl_step")):
            settings.pack_start(Gtk.Label(label=_t(label)), False, False, 0)
            ent = Gtk.Entry(); ent.set_width_chars(8); ent.set_text(str(value))
            ent.connect("activate", self._table_setting, attr)
            settings.pack_start(ent, False, False, 0)
        page.pack_start(settings, False, False, 0)
        self.table_grid = Gtk.Grid(column_spacing=20, row_spacing=4)
        scroll = Gtk.ScrolledWindow(); scroll.add(self.table_grid)
        page.pack_start(scroll, True, True, 0)
        return page

    def _on_y_changed(self, entry, i):
        self.ys[i] = entry.get_text(); self.graph.queue_draw()

    def _on_y_toggle(self, check, i):
        self.y_enabled[i] = check.get_active(); self.graph.queue_draw()

    def _table_setting(self, entry, attr):
        try:
            value = float(entry.get_text())
        except ValueError:
            return
        if attr == "tbl_start":
            self.tbl_start = value
        else:
            self.tbl_step = value
        self._refresh_table()

    def _eval_x(self, expression, x):
        old = self.expr
        self.expr = expression.replace("X", "(%r)" % x)
        result = self.evaluate()
        self.expr = old
        if result == "Error": raise ValueError(self._err_why)
        return float(result)

    def _draw_graph(self, area, cr):
        w, h = area.get_allocated_width(), area.get_allocated_height()
        cr.set_source_rgb(0.98, 0.97, 0.94); cr.paint()
        win = self.window
        cr.set_line_width(1)
        cr.set_source_rgb(0.84, 0.82, 0.76)
        for axis, scale in (("x", win["xscl"]), ("y", win["yscl"])):
            lo, hi = win[axis + "min"], win[axis + "max"]
            # A scale putting more gridlines across the view than it has
            # pixels would freeze the paint in this loop (xscl 1e-9 over a
            # 20-wide window is 2e10 iterations); such a grid is solid ink
            # anyway, so draw none for that axis.
            if scale <= 0 or (hi - lo) / scale > 400:
                continue
            n = math.ceil(lo / scale) * scale
            while n <= hi:
                px, py = graph_to_pixel(n if axis == "x" else 0,
                                        0 if axis == "x" else n, win, w, h)
                cr.move_to(px if axis == "x" else 0, 0 if axis == "x" else py)
                cr.line_to(px if axis == "x" else w, h if axis == "x" else py)
                n += scale
        cr.stroke(); cr.set_source_rgb(0.1, 0.1, 0.09)
        ox, oy = graph_to_pixel(0, 0, win, w, h)
        cr.move_to(0, oy); cr.line_to(w, oy); cr.move_to(ox, 0); cr.line_to(ox, h); cr.stroke()
        colors = ((0.78, .2, .12), (.1, .35, .55), (.3, .5, .2), (.45, .25, .5))
        for i, expression in enumerate(self.ys):
            if not self.y_enabled[i] or not expression.strip(): continue
            cr.set_source_rgb(*colors[i])
            for segment in sample_segments(lambda x, e=expression: self._eval_x(e, x),
                                           win["xmin"], win["xmax"], max(100, w)):
                for j, (x, y) in enumerate(segment):
                    px, py = graph_to_pixel(x, y, win, w, h)
                    (cr.move_to if j == 0 else cr.line_to)(px, py)
                cr.stroke()
        self._update_trace()
        return False

    def _on_graph_key(self, _area, event):
        name = Gdk.keyval_name(event.keyval)
        enabled = [i for i, on in enumerate(self.y_enabled) if on and self.ys[i].strip()]
        if name in ("Left", "Right"):
            self.trace_x += (-1 if name == "Left" else 1) * (self.window["xmax"] - self.window["xmin"]) / 100
        elif name in ("Up", "Down") and enabled:
            try: p = enabled.index(self.trace_curve)
            except ValueError: p = 0
            self.trace_curve = enabled[(p + (-1 if name == "Up" else 1)) % len(enabled)]
        else: return False
        self._update_trace(); self.graph.queue_draw(); return True

    def _update_trace(self):
        try: y = self._eval_x(self.ys[self.trace_curve], self.trace_x); sy = format_number(y, self.fix)
        except Exception: sy = _t("Undefined")
        self.trace_label.set_text("Y%d  X=%s  Y=%s" % (self.trace_curve + 1,
            format_number(self.trace_x, self.fix), sy))

    def _refresh_table(self):
        for child in self.table_grid.get_children(): self.table_grid.remove(child)
        enabled = [(i, e) for i, e in enumerate(self.ys) if self.y_enabled[i] and e.strip()]
        headers = ["X"] + ["Y%d" % (i + 1) for i, _e in enabled]
        for c, label in enumerate(headers): self.table_grid.attach(Gtk.Label(label=label), c, 0, 1, 1)
        for row in range(40):
            x = self.tbl_start + row * self.tbl_step
            self.table_grid.attach(Gtk.Label(label=format_number(x, self.fix)), 0, row + 1, 1, 1)
            for c, (_i, expression) in enumerate(enabled, 1):
                try: value = format_number(self._eval_x(expression, x), self.fix)
                except Exception: value = _t("Undefined")
                self.table_grid.attach(Gtk.Label(label=value), c, row + 1, 1, 1)
        self.table_grid.show_all()

    def _zoom(self, kind):
        if kind == "standard": self.window.update(xmin=-10., xmax=10., ymin=-10., ymax=10., xscl=1., yscl=1.)
        else:
            factor = .5 if kind == "in" else 2.
            if kind == "fit": factor = 1.
            if kind == "fit":
                vals = []
                for e in [e for i, e in enumerate(self.ys) if self.y_enabled[i] and e.strip()]:
                    for seg in sample_segments(lambda x, q=e: self._eval_x(q, x), self.window["xmin"], self.window["xmax"], 120): vals.extend(y for _x, y in seg if abs(y) < 1e6)
                if vals: self.window["ymin"], self.window["ymax"] = min(vals), max(vals)
            else:
                for a, b in (("xmin", "xmax"), ("ymin", "ymax")):
                    mid = (self.window[a] + self.window[b]) / 2; half = (self.window[b] - self.window[a]) * factor / 2
                    self.window[a], self.window[b] = mid - half, mid + half
        self.graph.queue_draw()

    def _window_dialog(self, *_):
        dialog = Gtk.Dialog(title=_t("Window"), transient_for=self, flags=Gtk.DialogFlags.MODAL)
        dialog.add_button(_t("Cancel"), Gtk.ResponseType.CANCEL); dialog.add_button(_t("Apply"), Gtk.ResponseType.OK)
        grid = Gtk.Grid(column_spacing=8, row_spacing=4); entries = {}
        for row, key in enumerate(("xmin", "xmax", "ymin", "ymax", "xscl", "yscl")):
            grid.attach(Gtk.Label(label=key), 0, row, 1, 1); ent = Gtk.Entry(); ent.set_text(str(self.window[key])); grid.attach(ent, 1, row, 1, 1); entries[key] = ent
        dialog.get_content_area().add(grid); dialog.show_all()
        if dialog.run() == Gtk.ResponseType.OK:
            try:
                values = {k: float(e.get_text()) for k, e in entries.items()}
                if values["xmin"] >= values["xmax"] or values["ymin"] >= values["ymax"] or values["xscl"] <= 0 or values["yscl"] <= 0: raise ValueError()
                self.window.update(values); self.graph.queue_draw()
            except ValueError: pass
        dialog.destroy()

    # ---- display ----
    def _display(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.get_style_context().add_class("display")

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        kick = Gtk.Label(label=_t("SCIENTIFIC"), xalign=0)
        kick.get_style_context().add_class("disp-kicker")
        top.pack_start(kick, False, False, 0)
        self.mode_lbl = Gtk.Label(label=_t("DEGREES"), xalign=1)
        self.mode_lbl.get_style_context().add_class("disp-mode")
        top.pack_end(self.mode_lbl, False, False, 0)
        box.pack_start(top, False, False, 0)

        tape_scroll = Gtk.ScrolledWindow()
        tape_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        tape_scroll.set_size_request(-1, 92)
        self.tape_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        tape_scroll.add(self.tape_box)
        box.pack_start(tape_scroll, False, False, 0)

        # Both readouts ellipsize from the START (the tail of a long expression
        # is the part you are still typing), and both cap their NATURAL width to
        # one character. Without that cap an ellipsizing label still asks for
        # the whole string as its natural size, so a long expression widened the
        # card — and with it the whole keypad — as you typed, and only started
        # ellipsizing once it hit the edge of the screen. Capped, the card keeps
        # its designed width and the text ellipsizes inside it.
        self.hist_lbl = Gtk.Label(label="", xalign=1)
        self.hist_lbl.set_hexpand(True)
        self.hist_lbl.set_ellipsize(Pango.EllipsizeMode.START)
        self.hist_lbl.set_max_width_chars(1)
        self.hist_lbl.get_style_context().add_class("disp-hist")
        # The history line is live: clicking it puts that calculation back on
        # the display to be edited and worked out again. An EventBox is what
        # gives a plain label a window to take the click, and it paints the
        # card's own paper so nothing shows through on the no-compositor stack.
        self._histbox = Gtk.EventBox()
        self._histbox.get_style_context().add_class("hist-box")
        self._histbox.set_can_focus(True)
        self._histbox.set_tooltip_text(_t("Recall last calculation"))
        self._histbox.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self._histbox.connect("button-press-event",
                              lambda *_: (self.recall(-1), True)[1])
        self._histbox.connect("key-press-event", self._on_history_key)
        self._histbox.add(self.hist_lbl)
        box.pack_start(self._histbox, False, False, 0)

        self.disp_lbl = Gtk.Label(label="0", xalign=1)
        self.disp_lbl.set_hexpand(True)
        self.disp_lbl.set_ellipsize(Pango.EllipsizeMode.START)
        self.disp_lbl.set_max_width_chars(1)
        self.disp_lbl.get_style_context().add_class("disp-main")
        box.pack_start(self.disp_lbl, False, False, 0)
        return box

    def _on_history_key(self, _box, event):
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            self.recall(-1)
            return True
        return False

    # ---- keypad ----
    def _keypad(self):
        grid = Gtk.Grid()
        grid.get_style_context().add_class("keypad")
        grid.set_row_spacing(1)
        grid.set_column_spacing(1)
        grid.set_column_homogeneous(True)
        grid.set_row_homogeneous(True)

        for i, kd in enumerate(KEYS):
            r, c = divmod(i, 6)
            label, action, value, ktype = kd
            btn = Gtk.Button()
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.set_hexpand(True)
            btn.set_vexpand(True)
            btn.get_style_context().add_class("key")
            btn.get_style_context().add_class("k-" + ktype)
            if action == "back":
                # ⌫ has no glyph in Nimbus Sans/Liberation — draw the pictographic
                # backspace icon natively (MTA monoline) instead of a label.
                child = nbicons.image("backspace", 22, "#1A1916")
            else:
                child = Gtk.Label()
                markup = SUP_MARKUP.get(value)
                if markup is not None:
                    # Power keys (xʸ, x²): draw the superscript from base-font
                    # characters via markup so the exponent never renders as
                    # tofu on a bare panel font.
                    child.set_markup(markup)
                else:
                    child.set_text(label)
            btn.add(child)
            tip = "Delete last entry" if action == "back" else TOOLTIPS.get(label)
            if tip is not None:
                btn.set_tooltip_text(tip)
            btn.connect("clicked", self._on_press, kd)
            grid.attach(btn, c, r, 1, 1)
            self._buttons.append((kd, btn, child))
            # Remember the few keys whose face/tooltip changes at runtime.
            if label == "DEG":
                self._deg_btn = (btn, child)
            elif label == "2nd":
                self._second_btn = btn
            elif label in INV_KEYS:
                self._inv_btns.append((label, btn, child))
        return grid

    # ---- logic ----
    def _on_press(self, _btn, kd):
        self.press(kd)

    def press(self, kd):
        label, action, value, ktype = kd
        # Any keypress dismisses a lingering "Error" display; "=" re-raises it
        # below if the fresh expression is still invalid.
        self.error = False
        # ...and ends any walk back through the history: from here on the
        # display is being typed, not browsed, so the next Up starts again from
        # the most recent calculation.
        self._tape_i = None
        if action == "second":
            self.second = not self.second
        elif action == "store":
            self._store_dialog()
        elif action == "catalog":
            self._catalog_dialog()
        elif action == "deg":
            self.deg = not self.deg
            self._save_prefs()
        elif action == "ac":
            self.expr = ""
            self.history = ""
            self.just_evaled = False
            self.second = False
        elif action == "back":
            self.expr = self.expr[:-1]
            self.just_evaled = False
        elif action == "neg":
            self.expr = ("−(" + self.expr + ")") if self.expr else "−"
            self.just_evaled = False
        elif action == "inv":
            # Reciprocal wraps the current expression as 1÷(…). With nothing
            # entered there is nothing to invert, so this is a no-op rather than
            # leaving a dangling "1÷(" that can only ever evaluate to "Error".
            if self.expr:
                self.expr = "1÷(" + self.expr + ")"
            self.just_evaled = False
        elif action == "eq":
            prev = self.expr
            if not prev.strip():
                self.second = False          # nothing to compute; stay at 0
            else:
                idx = self._remember(prev)
                r = self.evaluate()
                if r == "Error":
                    # Keep what was tried visible on the history line and show a
                    # clear, honest "Error" in the main display — never a silent
                    # "0" that reads as though the bad input equalled zero. The
                    # attempt keeps its tape slot (so Up can recall it to fix)
                    # but its result slot is None, which the tape never paints.
                    if idx is not None:
                        self.tape_results[idx] = None
                    self.history = prev.strip() + " ="
                    self.expr = ""
                    self.error = True
                    self.just_evaled = False
                else:
                    self.history = prev + " ="
                    self.expr = r
                    try: self.ans = float(r)
                    except ValueError: self.ans = r
                    if idx is not None:
                        self.tape_results[idx] = r
                    self.just_evaled = True
                    self._save_prefs()
                self.second = False
        elif action == "app":
            v = ALT_VALUE[value] if (self.second and value in ALT_VALUE) else value
            is_op = ktype == "op" or v in ("^", "%", "^2", "!", ")")
            if (not self.expr and ktype == "op" and v in ("+", "×", "÷", "^")
                    and any(r is not None for r in self.tape_results)):
                self.expr = "Ans"
            if self.just_evaled and not is_op:
                self.expr = v
            else:
                self.expr = self.expr + v
            self.just_evaled = False
            self.second = False
        self._refresh()

    # How many worked-out calculations are kept for Up / Down. Long enough to
    # cover a session's worth of adding things up, short enough that walking
    # back through it stays quick.
    _TAPE_MAX = TAPE_MAX

    def _remember(self, expr):
        """File an attempted expression, newest last, with a result slot filed
        alongside it in lockstep ("=" fills the slot on success, leaves it None
        on failure; the tape paints only filled slots). A repeat of the one
        already on top reuses its slot rather than being stored twice —
        pressing = on the same sum again should not fill the history with
        copies of it. Returns the tape index this attempt lives at, or None
        for an empty expression."""
        expr = expr.strip()
        if not expr:
            return None
        if not (self.tape and self.tape[-1] == expr):
            self.tape.append(expr)
            self.tape_results.append(None)
            del self.tape[:-self._TAPE_MAX]
            del self.tape_results[:-self._TAPE_MAX]
        return len(self.tape) - 1

    def recall(self, step):
        """Walk the history back (step -1) or forward (+1) into the display.

        Walking past the newest entry restores whatever was being typed when
        the walk started, so browsing the history never costs you the number
        you were part-way through. Returns True when something was recalled."""
        if not self.tape:
            return False
        if self._tape_i is None:
            if step > 0:
                return False             # nothing newer than what is showing
            self._tape_draft = self.expr
            self._tape_i = len(self.tape)
        i = max(self._tape_i + step, 0)
        if i >= len(self.tape):
            self._tape_i = None
            self.expr = self._tape_draft
        else:
            self._tape_i = i
            self.expr = self.tape[i]
        self.just_evaled = False
        self.error = False
        self._refresh()
        return True

    def _fail(self, why):
        """Record WHY the calculation failed and return the sentinel the
        caller already tests for, so no call site has to change."""
        self._err_why = why
        return "Error"

    def evaluate(self):
        # "Error" stays the sentinel the caller tests for; _err_why carries
        # the sentence the DISPLAY shows, so the person is told what went
        # wrong rather than only that something did. Reset on every attempt.
        self._err_why = None
        js = self.expr
        if not js.strip():
            return "0"
        js = (js.replace("×", "*").replace("÷", "/")
                .replace("−", "-").replace("π", "(PI)")
                .replace("√", "sqrt").replace("^", "**"))
        # Protect digits that are part of function names from the implicit
        # multiplication rule below (which correctly turns ordinary 2e into
        # 2*e, but must not turn log2 into log*2).
        js = js.replace("log2(", "logtwo(").replace("pow10(", "powten(")
        # percent: "+10%" / "-10%" is ten percent OF the left-hand value, any
        # other "n%" is n/100 (see _expand_percent)
        js = _expand_percent(js)
        js = _postfix_fact(js)
        if js is None:
            return self._fail(_WHY_UNREADABLE)

        # implicit multiplication: let a novice type "2π", "2(3)",
        # "(1+1)(2)", "3sin(0)", ")(" without getting "Error". Runs after the
        # symbol subs above (π->(PI), √->sqrt) so number/paren/constant
        # boundaries are explicit. Digit-before-e/E is deliberately skipped so
        # float scientific notation like "1e-05" (a previous result) still
        # parses.
        js = re.sub(r"\)\(", ")*(", js)                     # )(  and π( via (PI)(
        js = re.sub(r"(\d)\(", r"\1*(", js)                 # 2(  and 2π via 2(PI)
        js = re.sub(r"\)(\d)", r")*\1", js)                 # )2
        js = re.sub(r"\)([A-Za-z])", r")*\1", js)           # )sin , )e
        js = re.sub(r"(\d)([A-DF-Za-df-z])", r"\1*\2", js)  # 3sin (not 1e-05)
        js = re.sub(r"(?<=\d)e(?![+\-]?\d)", "*e", js)      # 2e -> 2*e (constant e, not 1e-05/2e3)
        js = re.sub(r"e\(", "e*(", js)                      # constant e then (

        # Forgive unclosed parentheses: a novice who types "√(9", "log(100",
        # "sin(30" or "(2+3" and presses = should get an answer, not "Error".
        # Every substitution above only adds balanced pairs (or a "*"), so any
        # leftover excess "(" is the user's own unclosed group — append the
        # matching ")" before parsing. Excess ")" is left to fail as "Error".
        opened = js.count("(") - js.count(")")
        if opened > 0:
            js += ")" * opened

        d2r = math.pi / 180 if self.deg else 1.0

        # Trig in DEGREES is answered from the degree value itself, not by
        # multiplying it into radians and trusting the result. pi has no exact
        # binary form, so radians(180) is not exactly pi and sin() of it came
        # back 1.22464679915e-16 — a calculator that answers sin(180) with
        # anything but 0 reads as broken, and every calculator on a desk says 0.
        # Reducing into one turn makes the four cardinal angles exact (and tan
        # at 90 and 270 the undefined value it really is, instead of the
        # 1.63312393532e+16 that float error invented). Every other angle is
        # left to math's own correctly-rounded answer, unchanged.
        def turn(x):
            """(angle within one turn, True) at a multiple of 90 degrees."""
            r = math.fmod(x, 360.0)
            if r < 0:
                r += 360.0
            return r, (r in (0.0, 90.0, 180.0, 270.0))

        def dsin(x):
            r, exact = turn(x)
            return (0.0, 1.0, 0.0, -1.0)[int(r / 90)] if exact \
                else math.sin(x * d2r)

        def dcos(x):
            r, exact = turn(x)
            return (1.0, 0.0, -1.0, 0.0)[int(r / 90)] if exact \
                else math.cos(x * d2r)

        def dtan(x):
            r, exact = turn(x)
            if not exact:
                return math.tan(x * d2r)
            if r in (90.0, 270.0):
                raise ValueError("tan is undefined here")   # -> "Error"
            return 0.0

        def fact(n):
            # Factorial is defined on whole numbers. Rounding 5.5 to 6 and
            # answering 720 would be a quiet lie about what was asked.
            if n != int(n):
                raise ValueError("factorial needs a whole number")
            n = int(n)
            if n < 0:
                return float("nan")
            if n > 10000:
                # cap: n! past this builds a multi-million-digit integer whose
                # multiply loop hangs/OOMs the app — refuse up front so we show
                # "Error" instead of freezing. (200! etc. still compute fine.)
                raise OverflowError("factorial too large")
            r = 1
            for i in range(2, n + 1):
                r *= i
            return r

        env = {
            "sin": dsin if self.deg else math.sin,
            "cos": dcos if self.deg else math.cos,
            "tan": dtan if self.deg else math.tan,
            "asin": lambda x: math.asin(x) / d2r,
            "acos": lambda x: math.acos(x) / d2r,
            "atan": lambda x: math.atan(x) / d2r,
            "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
            "ln": math.log,
            "log": math.log10,
            "logtwo": math.log2, "exp": math.exp,
            "powten": lambda x: _guarded_pow(10, x),
            "sqrt": math.sqrt,
            "root": lambda x, n: math.copysign(abs(x) ** (1.0 / n), x) if int(n) % 2 else x ** (1.0 / n),
            "abs": abs, "floor": math.floor, "ceil": math.ceil, "round": round,
            "frac": lambda x: x - math.trunc(x), "int": math.trunc,
            "nCr": lambda n, r: math.factorial(int(n)) // (math.factorial(int(r)) * math.factorial(int(n-r))) if n == int(n) and r == int(r) and 0 <= r <= n else (_ for _ in ()).throw(ValueError()),
            "nPr": lambda n, r: math.factorial(int(n)) // math.factorial(int(n-r)) if n == int(n) and r == int(r) and 0 <= r <= n else (_ for _ in ()).throw(ValueError()),
            "random": random.random,
            "fact": fact,
            "_pow": _guarded_pow,
            "PI": math.pi,
            "e": math.e,
            "Ans": getattr(self, "ans", 0),
        }
        env.update({k: v for k, v in getattr(self, "variables", {}).items() if re.match(r"^[A-Z]$", k)})
        try:
            # Rewrite  **  into bounded _pow() calls before evaluating so an
            # oversized power (9^9^9) is rejected up front instead of building a
            # giant integer that hangs/OOMs the app. A parse/syntax error on an
            # incomplete expression still degrades to "Error" as before.
            tree = ast.parse(js, mode="eval")
            _PowGuard().visit(tree)
            ast.fix_missing_locations(tree)
            r = eval(compile(tree, "<calc>", "eval"),  # noqa: S307
                     {"__builtins__": {}}, env)
        except ZeroDivisionError:
            return self._fail(_WHY_ZERO)
        except (OverflowError, MemoryError):
            return self._fail(_WHY_TOOBIG)
        except (SyntaxError, NameError):
            return self._fail(_WHY_UNREADABLE)
        except Exception:
            # ValueError from sqrt(-1), log(0), tan(90), a fractional
            # factorial: the expression reads fine, it just has no answer.
            return self._fail(_WHY_NOANSWER)
        if isinstance(r, complex) or r is None:
            return self._fail(_WHY_NOANSWER)
        # An INTEGER result is exact, and staying exact is worth more here than
        # a uniform twelve significant figures: 20! and 2^62 both fit on the
        # display to their last digit, and answering them "2.43290200818e+18"
        # throws away an answer the calculator already had in full. Past what
        # one line holds (200! is 375 digits, 10^400 is 401) the magnitude is
        # the readable answer instead — see _sci.
        if isinstance(r, int) and not isinstance(r, bool):
            try:
                s = str(r)
            except (ValueError, MemoryError):
                # str() of an int with > 4300 digits raises on Python 3.11+
                return self._fail(_WHY_TOOBIG)
            return s if len(s.lstrip("-")) <= _MAX_DIGITS else _sci(s)
        try:
            if math.isinf(r):
                return self._fail(_WHY_TOOBIG)
            if math.isnan(r):
                return self._fail(_WHY_NOANSWER)
            # match toPrecision(12) then trim
            r = float("%.12g" % r)
            if r == int(r) and abs(r) < 1e16:
                return format_number(r, getattr(self, "fix", None)) if getattr(self, "fix", None) is not None else str(int(r))
            return format_number(r, getattr(self, "fix", None)) if getattr(self, "fix", None) is not None else repr(r)
        except OverflowError:
            return self._fail(_WHY_TOOBIG)
        except (TypeError, ValueError):
            # a non-numeric result (e.g. a bare function name left by an
            # incomplete expression) reaches the numeric checks — degrade
            # to Error instead of crashing the app. (A huge integer, which
            # math.isinf() cannot coerce to a C double, is already handled
            # above and never reaches here.)
            return self._fail(_WHY_UNREADABLE)

    def _refresh(self):
        if hasattr(self, "tape_box"):
            for child in self.tape_box.get_children(): self.tape_box.remove(child)
            for expression, result in tape_window(self.tape, self.tape_results):
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
                row.pack_start(Gtk.Label(label=expression, xalign=0), True, True, 0)
                row.pack_end(Gtk.Label(label=result, xalign=1), False, False, 0)
                self.tape_box.pack_start(row, False, False, 0)
            self.tape_box.show_all()
        disp_ctx = self.disp_lbl.get_style_context()
        if self.error:
            # "Error" names nothing. Say which of the four things happened,
            # in the same red the failed "=" already used.
            self.disp_lbl.set_text(_t(self._err_why or _WHY_UNREADABLE))
            disp_ctx.add_class("err")
        else:
            self.disp_lbl.set_text(self.expr or "0")
            disp_ctx.remove_class("err")
        # History / mode: only re-set (and pay a relayout) when the text truly
        # changed — a redundant set_text still re-lays-out the label, wasted work
        # on every keypress on the CPU-only renderer.
        if self.history != self._hist_txt:
            self._hist_txt = self.history
            self.hist_lbl.set_text(self.history)
            # only offer the hint while there is actually something to click
            self._histbox.set_tooltip_text(
                _t("Click to use this calculation again")
                if self.history else None)
        mode = "DEGREES" if self.deg else "RADIANS"
        if mode != self._mode_txt:
            self._mode_txt = mode
            self.mode_lbl.set_text(mode)
        self._sync_dynamic_keys()

    def _sync_dynamic_keys(self):
        """Update only the keys whose face / tooltip / active-state can change
        (DEG-RAD, the sin/cos/tan inverse faces, the 2nd toggle). Every other key
        is static and is never touched, so typing a digit repaints only the
        display, not the whole keypad."""
        if self._deg_btn is not None:
            btn, lbl = self._deg_btn
            self._apply_face(lbl, "DEG" if self.deg else "RAD")
            self._apply_tip(
                btn, "Switch to radians" if self.deg else "Switch to degrees")
        for label, btn, lbl in self._inv_btns:
            if self.second:
                # superscript "-1" from base-font characters (never U+207B/U+00B9)
                self._apply_face(lbl, label + "<sup>-1</sup>", markup=True)
                self._apply_tip(btn, ALT_TOOLTIP[label])
            else:
                self._apply_face(lbl, label)
                self._apply_tip(btn, TOOLTIPS[label])
        if self._second_btn is not None:
            ctx = self._second_btn.get_style_context()
            if self.second:
                ctx.add_class("active")
            else:
                ctx.remove_class("active")

    def _apply_face(self, lbl, text, markup=False):
        """Set a key label's face, skipping the call (and its relayout) when the
        face is already what we want. `markup` picks set_markup vs set_text."""
        key = ("m" if markup else "t", text)
        if self._face_cache.get(lbl) == key:
            return
        self._face_cache[lbl] = key
        if markup:
            lbl.set_markup(text)
        else:
            lbl.set_text(text)

    def _apply_tip(self, btn, tip):
        if self._tip_cache.get(btn) == tip:
            return
        self._tip_cache[btn] = tip
        btn.set_tooltip_text(tip)

    # ---- menus ----
    def menu_items(self, name):
        # The base Edit acts on a focused text widget (Cut/Copy/Paste/Select
        # All) — this keypad has none, so those are dead. Replace with real
        # actions: copy the current result, and clear. View is empty in the base
        # (a dead button); give it a live Degrees/Radians angle-mode toggle. Both
        # radio items stay clickable (the active one just carries a bullet) so there
        # is never a permanently-greyed menu entry.
        if name == "Edit":
            return [
                (_t("Copy Result"), self._copy_result),
                # Up AND Down both walk the history (see _on_key_calc); only
                # one of them was on the menu, so half the feature had no way
                # to be discovered.
                (_t("Previous Calculation    Up"), lambda: self.recall(-1)),
                (_t("Next Calculation    Down"), lambda: self.recall(1)),
                nbapp.SEP,
                # The four-space column is the KEYBOARD shortcut, everywhere
                # in this OS. "AC" is the name of an on-screen key, so it
                # advertised a keystroke that does not exist; Del is the key
                # that actually does this (see _on_key_calc).
                (_t("Clear    Del"), self._clear_all),
                (_t("Store Variable…"), self._store_dialog),
            ]
        if name == "View":
            # A leading BULLET marks the active choice, the OS-wide convention
            # (Calendar, Tasks, Cookbook). U+2713 is in none of the bundled
            # Nimbus Sans faces, so the tick that used to be here was drawn
            # through CJK fallback in a foreign typeface -- see the same note
            # in screenplay.py. The label is translated here because nbapp
            # cannot: its _t() sees the marked label, which is not a catalog key.
            deg_mark = "•  " if self.deg else "    "
            rad_mark = "•  " if not self.deg else "    "
            return [
                (_t("Home    Ctrl+1"), lambda: self._switch_view("home")),
                (_t("Graph    Ctrl+2"), lambda: self._switch_view("graph")),
                (_t("Table    Ctrl+3"), lambda: self._switch_view("table")),
                (_t("Function Catalog…"), self._catalog_dialog),
                (_t("Variables…"), self._variables_dialog),
                nbapp.SEP,
                (deg_mark + _t("Degrees"), lambda: self._set_deg(True)),
                (rad_mark + _t("Radians"), lambda: self._set_deg(False)),
                ((_t("Float") if self.fix is None else _t("Fix %d") % self.fix), self._display_mode_dialog),
            ]
        return super().menu_items(name)

    def _copy_result(self):
        """Copy the current display value to the system clipboard, so a result
        can be pasted into another app (the keypad has no selectable text)."""
        try:
            clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clip.set_text(self.disp_lbl.get_text(), -1)
            clip.store()
        except Exception:
            pass

    def _set_deg(self, value):
        """Set degrees (True) / radians (False) explicitly from the View menu,
        persist it, and repaint the DEG key + mode line to match."""
        self.deg = value
        self._save_prefs()
        self._refresh()

    def _clear_all(self):
        self.press(("AC", "ac", None, "clear"))

    def _store_dialog(self):
        dialog = Gtk.Dialog(title=_t("Store Variable"), transient_for=self, flags=Gtk.DialogFlags.MODAL)
        dialog.add_button(_t("Cancel"), Gtk.ResponseType.CANCEL); dialog.add_button(_t("Store"), Gtk.ResponseType.OK)
        entry = Gtk.Entry(); entry.set_max_length(1); entry.set_placeholder_text(_t("Letter A-Z")); dialog.get_content_area().add(entry); dialog.show_all()
        if dialog.run() == Gtk.ResponseType.OK and re.match(r"^[A-Za-z]$", entry.get_text()):
            try: self.variables[entry.get_text().upper()] = float(self.expr)
            except ValueError: pass
            self._save_prefs()
        dialog.destroy()

    def _variables_dialog(self):
        dialog = Gtk.Dialog(title=_t("Variables"), transient_for=self, flags=Gtk.DialogFlags.MODAL)
        dialog.add_button(_t("Close"), Gtk.ResponseType.CLOSE)
        text = "\n".join("%s = %s" % item for item in sorted(self.variables.items())) or _t("No Stored Variables")
        dialog.get_content_area().add(Gtk.Label(label=text, xalign=0)); dialog.show_all(); dialog.run(); dialog.destroy()

    def _catalog_dialog(self):
        dialog = Gtk.Dialog(title=_t("Function Catalog"), transient_for=self, flags=Gtk.DialogFlags.MODAL)
        dialog.add_button(_t("Cancel"), Gtk.ResponseType.CANCEL); dialog.add_button(_t("Insert"), Gtk.ResponseType.OK)
        store = Gtk.TreeStore(str, str)
        for category, items in CATALOG.items():
            parent = store.append(None, [_t(category), ""])
            for label, value in items: store.append(parent, [label, value])
        tree = Gtk.TreeView(model=store); cell = Gtk.CellRendererText(); tree.append_column(Gtk.TreeViewColumn(_t("Function"), cell, text=0)); tree.expand_all()
        scroll = Gtk.ScrolledWindow(); scroll.set_size_request(320, 360); scroll.add(tree); dialog.get_content_area().add(scroll); dialog.show_all()
        if dialog.run() == Gtk.ResponseType.OK:
            model, it = tree.get_selection().get_selected()
            if it is not None and model[it][1]: self._press_value(model[it][1])
        dialog.destroy()

    def _display_mode_dialog(self):
        dialog = Gtk.Dialog(title=_t("Display Mode"), transient_for=self, flags=Gtk.DialogFlags.MODAL)
        dialog.add_button(_t("Cancel"), Gtk.ResponseType.CANCEL); dialog.add_button(_t("Apply"), Gtk.ResponseType.OK)
        combo = Gtk.ComboBoxText(); combo.append_text(_t("Float"))
        for i in range(10): combo.append_text(_t("Fix %d") % i)
        combo.set_active(0 if self.fix is None else self.fix + 1); dialog.get_content_area().add(combo); dialog.show_all()
        if dialog.run() == Gtk.ResponseType.OK: self.fix = None if combo.get_active() == 0 else combo.get_active() - 1; self._save_prefs()
        dialog.destroy()

    # ---- persistence ----
    def _load_prefs(self):
        """Return the stored angle mode (True = degrees), or the degrees default
        when the file is missing or malformed. Must never crash the launch."""
        try:
            with open(STATE_FILE) as fh:
                data = json.load(fh)
            deg = data.get("deg")
            if isinstance(deg, bool):
                return deg
        except Exception:
            pass
        return True

    def _load_state(self):
        self._store_readable = True
        try:
            with open(STATE_FILE) as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception:
            self._store_readable = False
            return {}

    def _save_prefs(self):
        """Persist the angle mode to this app's private JSON file. Never crash
        on I/O — a read-only or missing config dir just skips the save."""
        try:
            if not self._store_readable:
                return
            nbapp.atomic_write_json(STATE_FILE, {
                "deg": bool(self.deg), "fix": self.fix, "ans": self.ans,
                "tape": self.tape, "tape_results": self.tape_results,
                "variables": self.variables, "ys": self.ys,
                "y_enabled": self.y_enabled, "window": self.window,
                "tbl_start": self.tbl_start, "tbl_step": self.tbl_step,
                "trace_x": self.trace_x})
        except Exception:
            pass

    def _on_destroy(self, *_):
        self._save_prefs()
        return False

    # ---- keyboard ----
    def _on_key_calc(self, _w, ev):
        kv = ev.keyval
        name = Gdk.keyval_name(kv)
        # Ctrl+C copies the current result — the same action as Edit ▸ Copy
        # Result, so the usual keyboard reflex just works.
        if (ev.state & Gdk.ModifierType.CONTROL_MASK) and name in ("c", "C"):
            self._copy_result()
            return True
        if ev.state & Gdk.ModifierType.CONTROL_MASK:
            if name in ("1", "2", "3"):
                self._switch_view({"1": "home", "2": "graph", "3": "table"}[name])
                return True
            if name in ("m", "M"):
                self._catalog_dialog(); return True
            if name in ("s", "S"):
                self._store_dialog(); return True
        if name == "Escape":
            if getattr(self, "current_view", "home") != "home":
                self._switch_view("home"); return True
            return False
        table = {
            "0": "0", "1": "1", "2": "2", "3": "3", "4": "4",
            "5": "5", "6": "6", "7": "7", "8": "8", "9": "9",
            "period": ".", "KP_Decimal": ".",
            # A comma is a decimal point on most of the keyboards this OS
            # ships a layout for — French, German, Spanish, Italian, Russian,
            # Polish, Portuguese, Turkish. Without these two the decimal key
            # on the main row was DEAD, and on a laptop with no numpad that is
            # the only decimal key there is: typing "3,5" produced 35.
            # It inserts "." because that is what the display shows and what
            # the parser reads; the point is that the key does something and
            # what it does is visible, not that the separator is localised.
            "comma": ".", "KP_Separator": ".",
            "parenleft": "(", "parenright": ")",
            "plus": "+", "KP_Add": "+",
            "minus": "−", "KP_Subtract": "−",
            "asterisk": "×", "KP_Multiply": "×",
            "slash": "÷", "KP_Divide": "÷",
            "percent": "%", "asciicircum": "^", "exclam": "!",
        }
        for i in range(10):
            table["KP_" + str(i)] = str(i)
        if name in table:
            self._press_value(table[name])
            return True
        if name and len(name) == 1 and name.isalpha():
            self._press_value(name.upper()); return True
        # Up / Down walk the history, the way they do at any command line.
        # They are swallowed either way so a stray Up never silently moves the
        # keyboard focus around the keypad instead; Tab and Left/Right still do
        # that, and every key on the pad can be typed directly.
        if name in ("Up", "KP_Up"):
            self.recall(-1)
            return True
        if name in ("Down", "KP_Down"):
            self.recall(1)
            return True
        if name in ("Return", "KP_Enter", "equal"):
            self.press(("=", "eq", None, "eq"))
            return True
        if name == "BackSpace":
            self.press(("⌫", "back", None, "clear"))
            return True
        if name in ("Delete",):
            self.press(("AC", "ac", None, "clear"))
            return True
        return False

    def _press_value(self, v):
        ktype = "num" if (v.isdigit() or v == ".") else "op"
        if v in ("%", "^", "(", ")"):
            ktype = "fn"
        self.press((v, "app", v, ktype))

    # ---- css ----
    def _install_css(self):
        css = b"""
        /* Desk tone shared OS-wide (calendar/g2048/illustrator). The scroller
           and its viewport paint the same OPAQUE desk so an over-scrolled or
           short panel never exposes a transparent (black) window surface. */
        .calcscroll, .calcscroll viewport { background: #DED4C2; }
        .calcstage { background: #DED4C2; padding: 40px; }
        .calcnav { background: #F1EEE6; border-bottom: 1px solid #C9C4B6; }
        .calcnav button { border-radius: 0; min-height: 34px; }
        .calcnav button.active { background: #C8341E; color: #FCFBF8; }
        .graphpage, .tablepage { background: #F8F7F2; padding: 14px; }
        .calccard { background: #F8F7F2; border: 1px solid #1A1916;
                    box-shadow: 4px 4px 0 rgba(26,25,22,0.12); }
        .calccard * { font-family: "Nimbus Sans","Helvetica",sans-serif; }

        .display { padding: 30px 28px 26px; border-bottom: 1px solid #1A1916; }
        .disp-kicker { font-size: 11px; letter-spacing: 0.16em;
                       color: #9A9484; font-weight: 600; }
        .disp-mode { font-size: 12px; letter-spacing: 0.08em; color: #8A857A;
                     font-weight: 600; }
        /* the history line's click target paints the card's own paper: never
           leave a bare EventBox transparent on the no-compositor stack */
        .hist-box { background: #F8F7F2; }
        .hist-box:focus { outline: 2px solid #1A1916; outline-offset: -2px; }
        .disp-hist { font-size: 15px; color: #9A9484; margin-top: 14px;
                     min-height: 20px; }
        /* min-height holds the display box at its full-size height, so the
           shorter error sentence does not shrink the card and shift the
           whole keypad up under the pointer. */
        .disp-main { font-size: 52px; font-weight: 500; color: #1A1916;
                     letter-spacing: -0.01em; margin-top: 4px;
                     min-height: 62px; }
        /* Signage-red marks the alert state. The error state is a SENTENCE,
           not the word "Error", so it is set at a size that fits the display
           instead of ellipsizing to "...o that" at 52px. The card is centred in
           the window, so the shorter line just re-centres. */
        .disp-main.err { color: #C8341E; font-size: 20px; font-weight: 600; }

        .keypad { background: #D7D2C5; }
        .key { border: none; border-radius: 0; box-shadow: none;
               min-height: 66px; padding: 0; }
        .key label { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .k-num        { background: #F8F7F2; color: #1A1916; }
        .k-num label  { font-size: 24px; font-weight: 500; }
        .k-num:hover  { background: #EFEBE0; }
        .k-op         { background: #EAE3D2; color: #1A1916; }
        .k-op label   { font-size: 24px; }
        /* #D7D2C5 is the shared light-hairline tone (OS-wide): the canonical
           darken of the operator key on hover, replacing a one-off swatch. */
        .k-op:hover   { background: #D7D2C5; }
        .k-eq         { background: #C8341E; color: #FCFBF8; }
        .k-eq label   { font-size: 24px; font-weight: 600; }
        /* #B12D19 is the canonical hover darkening of the brand red #C8341E,
           shared OS-wide so every red button darkens to the same shade. */
        .k-eq:hover   { background: #B12D19; }
        .k-clear      { background: #EFEBE0; color: #1A1916; }
        .k-clear label{ font-size: 17px; font-weight: 600; }
        .k-clear:hover{ background: #DED4C2; }
        .k-fn         { background: #EFEBE0; color: #3A362E; }
        .k-fn label   { font-size: 16px; }
        .k-fn:hover   { background: #DED4C2; }
        /* signage-red marks the active/selected state (2nd toggled on). */
        .key.active         { background: #C8341E; color: #FCFBF8; }
        .key.active:hover   { background: #B12D19; }
        .key.active label   { color: #FCFBF8; }
        """
        prov = Gtk.CssProvider()
        prov.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), prov,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)


if __name__ == "__main__":
    nbapp.run(Calculator)
