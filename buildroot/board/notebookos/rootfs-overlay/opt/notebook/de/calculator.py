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
import nbcommands
import nbtransitions
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
MAX_STATE_BYTES = 8 * 1024 * 1024


class CalculatorStoreTooLarge(ValueError):
    pass


def _read_state_json(path=None, limit=MAX_STATE_BYTES):
    if path is None:
        path = STATE_FILE
    with open(path, "rb") as source:
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise CalculatorStoreTooLarge("calculator state is too large")
    return json.loads(raw)

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

# The names a person can TYPE, keyed by their lower-case spelling. The keypad
# and the catalog insert these in their own case ("sin(", "nCr(", "Ans"); the
# key ladder makes every letter an uppercase VARIABLE, so a keyboard produced
# "SIN(30)" and evaluate() knew no such name -- the one path a laptop user has
# to a function was the paste path. A run of two or more letters is matched
# here without regard to case (a single letter stays a variable, so A..Z and
# the graph's X are untouched), and press() spells a typed name the keypad's
# way the moment its "(" arrives, so the display reads sin(30) too.
_NAME_BY_LOWER = {}
for _items in CATALOG.values():
    for _label, _value in _items:
        _name = _value.rstrip("()")
        _NAME_BY_LOWER[_name.lower()] = _name
_NAME_BY_LOWER.update({"ans": "Ans", "pi": "PI"})
_TYPED_NAME = re.compile(r"[A-Za-z][A-Za-z0-9]*")


# The three things this keypad can write that take TWO arguments. Every one of
# them is reachable only through the MATH catalog, and every one of them was
# impossible to finish: the comma key inserts a DECIMAL POINT (see the key
# table in _on_key_calc -- on the French, German, Spanish, Italian, Russian,
# Polish, Portuguese and Turkish layouts this OS ships, comma is the decimal
# key and without that mapping the main-row decimal point is dead). So
# nCr( from the catalog, 5, comma, 2, ")" produced "nCr(5.2)" and "="
# answered "There is no answer to that" -- blaming the person for a separator
# the calculator gave them no way to type. Inside one of these three calls,
# and only there, the comma key is the ARGUMENT SEPARATOR it has to be.
TWO_ARGUMENT = ("nCr", "nPr", "root")


def wants_argument(expr):
    """Is `expr` inside an unclosed two-argument call still missing its comma?

    Written as what the separator case IS, so an expression that is not one
    (2.5, sin(30, an already-separated nCr(5,2) keeps its decimal point."""
    if not expr:
        return False
    depth, i = 0, len(expr)
    while i > 0:
        c = expr[i - 1]
        if c == ")":
            depth += 1
        elif c == "(":
            if depth == 0:
                break
            depth -= 1
        i -= 1
    if i == 0:
        return False                       # nothing is open
    j = i - 1                              # the name in front of that "("
    while j > 0 and (expr[j - 1].isalnum() or expr[j - 1] == "_"):
        j -= 1
    if expr[j:i - 1] not in TWO_ARGUMENT:
        return False
    level = 0                              # a comma of its OWN, not a nested one
    for c in expr[i:]:
        if c == "(":
            level += 1
        elif c == ")":
            level -= 1
        elif c == "," and level == 0:
            return False
    return True


def canonical_names(text):
    """Spell every multi-letter function/constant name in `text` the way
    evaluate() knows it (SIN( -> sin(, ANS -> Ans). Single letters and unknown
    words are left exactly as they are."""
    def fix(m):
        word = m.group(0)
        if len(word) < 2:
            return word
        return _NAME_BY_LOWER.get(word.lower(), word)
    return _TYPED_NAME.sub(fix, text)


# Below this fraction of its larger operand, a sum or difference is float
# noise, not an answer: 0.1+0.2-0.3 came back 5.55111512313e-17 because
# 0.1+0.2 is 0.30000000000000004 in binary, and a calculator that says so
# reads as broken. Twelve significant figures is what the display shows, so
# a difference smaller than a thirteenth figure of the operands is one the
# operands themselves could not carry. Applied to + and - ONLY (see _OpGuard):
# 1e-20 is a real answer to 1÷10^20, and stays one.
_SUM_NOISE = 1e-13


def _snap_sum(r, a, b):
    try:
        if r and abs(r) < _SUM_NOISE * max(abs(a), abs(b)):
            return 0.0
    except TypeError:
        pass
    return r


def _add(a, b):
    return _snap_sum(a + b, a, b)


def _sub(a, b):
    return _snap_sum(a - b, a, b)


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
MAX_EXPRESSION_CHARS = 256
# The widest answer this calculator prints: a 20-digit exact integer (_MAX_DIGITS)
# and its sign, or a Fix 9 rendering, whichever is longer. What a tape row's
# result column may ask the card for.
_TAPE_RESULT_CHARS = 22


def append_expression(expr, value, limit=MAX_EXPRESSION_CHARS):
    """Append one keypad token only when the expression stays bounded."""
    return expr + value if len(expr) + len(value) <= limit else expr

_WINDOW_DEFAULT = {"xmin": -10.0, "xmax": 10.0, "ymin": -10.0, "ymax": 10.0,
                   "xscl": 1.0, "yscl": 1.0}


def _damaged_note():
    """What the calculator says when it could not read what it saved.

    Two sentences, deliberately, and the second is the one that matters: the
    person's stored variables and tape are not on screen, that is not something
    they did, and the file they came from is still on the disk under a
    .damaged-<stamp> name. Without saying so, the only reasonable conclusion is
    that the work is gone -- and the natural response, retyping it, is exactly
    what would make that true.

    The second sentence is accounting's, keyed identically on purpose: the same
    fact in the same words costs one translation across both apps rather than
    two near-misses a reader would have to notice are the same thing."""
    return (_t("The saved calculator could not be read. A new one was started.")
            + " " + _t("The damaged file was kept."))


def _finite(value, fallback):
    """float(value) when it names a real, finite number; the fallback else."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return fallback
    return v if math.isfinite(v) else fallback


def window_is_valid(values):
    """Is this a window a graph can actually be drawn in?

    Lifted out of the Window dialog so it can be CHECKED. Inside the dialog it
    sat behind a `dialog.run()`, which no test can drive without a modal loop,
    and the one condition it was missing is exactly the sort a test finds:
    every comparison here is False when either side is NaN, so an all-NaN
    window satisfied all four ordering rules and the next draw died with
    "cannot convert float NaN to integer". float() accepts "nan" and "inf"
    without complaint.

    Finiteness is therefore checked FIRST and separately, the same screen
    sanitize_state already puts the stored window through.

    Note which half actually closed the NaN hole, because it is not the obvious
    one: the rule below is written POSITIVELY -- what a good window IS -- and
    NaN fails a positive test. The shipped guard was the negative of it,
    `if xmin >= xmax or ...: raise`, and NaN fails that too, so it was let
    through. Measured both ways. The isfinite line is what catches inf, which
    orders perfectly well and is still not a window to draw in."""
    try:
        if not all(math.isfinite(float(v)) for v in values.values()):
            return False
        return (values["xmin"] < values["xmax"]
                and values["ymin"] < values["ymax"]
                and values["xscl"] > 0 and values["yscl"] > 0)
    except (TypeError, ValueError, KeyError):
        return False


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

    known = {"tape", "tape_results", "variables", "ans", "fix", "ys",
             "y_enabled", "window", "tbl_start", "tbl_step", "trace_x",
             "deg", "_extra"}
    extra = dict(state.get("_extra")) if isinstance(state.get("_extra"), dict) else {}
    extra.update((k, v) for k, v in state.items() if k not in known)

    # ...and bounded, the same screen the graph's ys gets: a hand-edited or
    # older store must not be able to hand the display a string longer than
    # anything the keypad can produce.
    raw = state.get("tape")
    tape = ([str(x)[:MAX_EXPRESSION_CHARS] for x in raw]
            if isinstance(raw, list) else [])
    raw = state.get("tape_results")
    results = ([None if x is None else str(x)[:MAX_EXPRESSION_CHARS] for x in raw]
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
        # Window metadata owned by a newer graphing build must ride through an
        # older one. Current geometry keys below remain authoritative and are
        # still forced finite/ordered, so preservation cannot bypass safety.
        window.update((k, v) for k, v in raw.items()
                      if k not in _WINDOW_DEFAULT)
        for key in window:
            if key in _WINDOW_DEFAULT:
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
        # ...and clamped into it, so a trace saved off the edge by an older
        # build (or by a hand-edited file) comes back on screen rather than
        # reopening in the same lost place.
        "trace_x": min(window["xmax"], max(window["xmin"],
                                           _finite(state.get("trace_x"), 0.0))),
        "tbl_start": _finite(state.get("tbl_start"), 0.0),
        # ...and never zero, or every row of the table is the same x. _finite
        # screens nan/inf; zero parses perfectly well and is still not a step.
        "tbl_step": _finite(state.get("tbl_step"), 1.0) or 1.0,
        "deg": deg if isinstance(deg, bool) else True, "_extra": extra,
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

# The function strip is eight keys wide; everything after it folds at six.
# These two numbers are the layout — see _keypad for what went wrong when the
# whole list was folded at a single width.
STRIP_KEYS = 8
PAD_COLS = 6

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
# STO-> and MATH were the only two OPAQUE keys on the pad with no tooltip, and
# they are the two that open a dialog -- the ones most in need of saying what
# they do. Both reuse the wording of their own menu item, which is already
# translated in all 17 catalogs, so naming them cost no new strings and the key
# and the menu entry now say the same thing.
TOOLTIPS = {
    "STO→": "Store Variable…",
    "MATH": "Function Catalog…",
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


_PCT_NUMBER = r"(?:\d+(?:\.\d*)?|\.\d+)"
_PCT_REL = re.compile(r"([+\-])(" + _PCT_NUMBER + r")%")
_PCT_ANY = re.compile(r"(" + _PCT_NUMBER + r")%")


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


# Mangled source -> compiled code. The graph evaluates ONE expression at 401
# points per curve per redraw, so without this every sample re-parses and
# re-compiles the same text. Keyed on the mangled string, which is what actually
# gets compiled, and cleared wholesale rather than evicted one at a time: this
# is a calculator, the working set is a handful of expressions, and a plain dict
# with a ceiling costs nothing to reason about.
_CODE_CACHE = {}
_CODE_CACHE_MAX = 256


class _OpGuard(ast.NodeTransformer):
    # Rewrite every  a ** b  into  _pow(a, b)  so nested/oversized powers are
    # bounded at runtime. A purely static exponent cap can't see that 9**9**9
    # actually means 9**(9**9) (each literal exponent is just 9).
    # ...and every  a + b  /  a - b  into _add / _sub, which snap a result
    # that is only float noise of its operands to 0 (see _SUM_NOISE).
    _CALLS = {ast.Pow: "_pow", ast.Add: "_add", ast.Sub: "_sub"}

    def visit_BinOp(self, node):
        self.generic_visit(node)
        name = self._CALLS.get(type(node.op))
        if name is not None:
            return ast.copy_location(
                ast.Call(func=ast.Name(id=name, ctx=ast.Load()),
                         args=[node.left, node.right], keywords=[]),
                node)
        return node


class _ArithmeticGuard(ast.NodeVisitor):
    """Accept calculator arithmetic, never general Python expressions."""
    _BIN = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow)
    _UNARY = (ast.UAdd, ast.USub)

    def __init__(self, names):
        self.names = set(names)

    def reject(self):
        raise SyntaxError("not calculator arithmetic")

    def generic_visit(self, _node):
        self.reject()

    def visit_Expression(self, node):
        self.visit(node.body)

    def visit_Constant(self, node):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            self.reject()

    def visit_Name(self, node):
        if node.id not in self.names:
            self.reject()

    def visit_BinOp(self, node):
        if not isinstance(node.op, self._BIN):
            self.reject()
        self.visit(node.left)
        self.visit(node.right)

    def visit_UnaryOp(self, node):
        if not isinstance(node.op, self._UNARY):
            self.reject()
        self.visit(node.operand)

    def visit_Call(self, node):
        if (not isinstance(node.func, ast.Name)
                or node.func.id not in self.names or node.keywords):
            self.reject()
        for arg in node.args:
            self.visit(arg)


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
        self._extra = state["_extra"]
        self.just_evaled = False
        self.second = False
        self.error = False   # last "=" failed; the display says why, in red
        self._closed = False  # async clipboard replies must stop at destroy
        self._err_why = None  # which _WHY_* sentence to show while error
        self._value = None    # the NUMBER the last answer is (see _answer)
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
        shell.get_style_context().add_class("calcroot")
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
        # Keep the card capped and centred with papertone flanks: stretching a
        # keypad to the full 1280px allocation would hurt the paper/letterpress
        # grammar and make its keys absurdly wide.
        # Card width tracks the real panel, never a hardcoded 1920/1080 stack:
        # 640 on a normal desk, but capped to the live screen (minus the desk
        # margin) so the card can never overflow a small 1366x768 / 1280x800
        # panel. min 320 keeps the keypad usable on an unusually narrow screen.
        sw, sh = nbapp.screen_size()
        card.set_size_request(max(320, min(640, sw - 96)), -1)
        # ...and the HEIGHT, which this line used to discard as `_sh`. The card
        # wants 732px; a 1024x768 panel leaves it 595 once the shell strut, the
        # view bar and the stage's own padding are taken out, so the bottom of
        # the keypad — "=" among it — sat below the fold and had to be scrolled
        # to. minsize_sweep cannot see this: it measures the WINDOW, and the
        # home page is inside a ScrolledWindow that reports a small fixed
        # minimum whatever the card holds, so the app passed at 1024x722 while
        # being unusable on one. Compact trims padding, key height and the tape
        # window; nothing is removed and no font drops below 13px.
        self._compact = sh < 860
        if self._compact:
            shell.get_style_context().add_class("compact")
        card.set_halign(Gtk.Align.CENTER)
        card.set_valign(Gtk.Align.CENTER)
        stage.set_halign(Gtk.Align.CENTER)
        stage.set_valign(Gtk.Align.CENTER)
        stage.pack_start(card, False, False, 0)

        card.pack_start(self._damage_strip(), False, False, 0)
        card.pack_start(self._display(), False, False, 0)
        card.pack_start(self._keypad(), False, False, 0)
        self.stack.add_named(self._graph_page(), "graph")
        self.stack.add_named(self._table_page(), "table")
        self._switch_view("home")

        self.connect("key-press-event", self._on_key_calc)
        self.connect("destroy", self._on_destroy)
        self._refresh()
        # Revealed rather than packed visible, so it SETTLES in with the rest of
        # the card instead of being there before the window has finished drawing
        # itself (Amendment 3: every state change travels).
        if self._damaged:
            nbtransitions.reveal(self.damage_rev, True)

    def _switch_view(self, name):
        # Leaving the Table page is leaving its two fields: a number typed into
        # Table Start / Table Step and not yet entered is taken here, the same
        # way it is taken when the field loses focus. Without this the field
        # kept the typed value, the table kept the old one, and coming back
        # re-synced the field -- throwing the typed number away in silence.
        if getattr(self, "current_view", None) == "table" and name != "table":
            for attr, entry in getattr(self, "tbl_entries", {}).items():
                self._table_setting(entry, attr)
        self.current_view = name
        self.stack.set_visible_child_name(name)
        for key, button in self._views.items():
            ctx = button.get_style_context()
            (ctx.add_class if key == name else ctx.remove_class)("active")
        if name == "table":
            self._sync_table_entries()
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
            # Preferences are external to this process too: keep an older or
            # hand-edited oversized curve out of the first graph redraw.
            self.ys[i] = self.ys[i][:MAX_EXPRESSION_CHARS]
            check = Gtk.CheckButton(label="Y%d" % (i + 1))
            check.set_active(bool(self.y_enabled[i]))
            entry = Gtk.Entry()
            entry.set_max_length(MAX_EXPRESSION_CHARS)
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
        self.graph.set_tooltip_text(_t("Graph"))
        try:
            self.graph.get_accessible().set_name(_t("Graph"))
        except Exception:
            pass
        self.graph.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.graph.connect("draw", self._draw_graph)
        self.graph.connect("button-press-event", self._on_graph_press)
        self.graph.connect("key-press-event", self._on_graph_key)
        page.pack_start(self.graph, True, True, 0)
        self.trace_label = Gtk.Label(label="", xalign=0)
        page.pack_start(self.trace_label, False, False, 0)
        return page

    def _table_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        page.get_style_context().add_class("tablepage")
        settings = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.tbl_entries = {}
        for label, value, attr in (("Table Start", self.tbl_start, "tbl_start"),
                                   ("Table Step", self.tbl_step, "tbl_step")):
            settings.pack_start(Gtk.Label(label=_t(label)), False, False, 0)
            ent = Gtk.Entry(); ent.set_width_chars(8)
            ent.set_text(format_number(value))
            ent.connect("activate", self._table_setting, attr)
            # A value typed and then left -- the person clicked into the
            # table, or went to the Graph page -- is taken (or put back) the
            # moment the field loses focus, so the field never keeps showing
            # a number the table is not using.
            ent.connect("focus-out-event", self._table_setting_left, attr)
            settings.pack_start(ent, False, False, 0)
            self.tbl_entries[attr] = ent
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
        """Take a typed Table Start / Table Step, or leave the old one alone.

        `float()` alone was not enough, the same way it was not enough for the
        graph window. Measured on the module as it stood, with the table showing
        Y1 = X:

            Table Step = 0      every one of the 40 rows read x = 0
            Table Step = nan    every x was nan
            Table Step = inf    every x was inf, and the first was nan
            Table Step = 1e400  parses to inf, same as above

        A step of zero is the interesting one: it is a plausible typo, it is
        accepted in silence, and it produces a table that looks broken with
        nothing to say why. Written as what a good value IS rather than as a
        list of bad ones — NaN fails a positive test, and passes a negative one,
        which is the shape of bug this app already had in window_is_valid."""
        try:
            value = float(entry.get_text())
        except ValueError:
            value = None
        if value is not None and not math.isfinite(value):
            value = None
        if value is not None and attr == "tbl_start":
            self.tbl_start = value
        elif value is not None and value != 0:  # a step of 0 is 40 copies of one row
            self.tbl_step = value
        else:
            # Refused -- and SAID so, by putting the value the table is really
            # using back into the field. Left as typed, the field read "0" or
            # "abc" over a table plainly stepping by 0.5, contradicting itself
            # in silence.
            entry.set_text(format_number(getattr(self, attr)))
            return
        self._refresh_table()

    def _table_setting_left(self, entry, _event, attr):
        self._table_setting(entry, attr)
        return False

    def _sync_table_entries(self):
        """Show in the two fields the values the table is built from."""
        for attr, entry in getattr(self, "tbl_entries", {}).items():
            live = format_number(getattr(self, attr))
            if entry.get_text() != live:
                entry.set_text(live)

    def _eval_x(self, expression, x):
        """Evaluate a graph function at one x.

        This used to substitute the sample INTO the source -- `sin(X)` became
        `sin((1.2345))` -- which meant every one of the 401 samples in a curve
        was a brand-new string that had to be re-mangled, re-parsed, re-guarded
        and re-compiled from scratch.

        MEASURED, interleaved before/after over five rounds so machine drift
        cancels -- a single A-then-B pair on a shared box is not a measurement,
        and mine said 8x on the first pair and 1.6x on the second:

            before   median 129.0 ms   (min 86.7, max 238.1)
            after    median  39.8 ms   (min 16.6, max  44.7)
                     ~3.2x on medians, 5.2x on bests

        That is roughly 8fps becoming roughly 25fps on the software renderer
        these machines actually use, and every zoom, pan and arrow-key trace
        paid the old cost again.

        X is bound as a NAME instead. Single uppercase letters are already this
        calculator's variables, so the environment resolves it with no new
        machinery, the source string stays identical across the whole curve, and
        the compiled form is reused. Same substitution semantics as before: a
        stored variable called X is shadowed by the sample either way."""
        old_expr = self.expr
        old_bind = getattr(self, "_x_bind", None)
        # ...and the two fields evaluate() writes about the LAST answer. A
        # sample is not an answer: without this, drawing the graph rewrote the
        # sentence a failed "=" had left on the home display, so 10/0 said
        # "Cannot divide by zero", a look at the graph went by, and the next
        # repaint said "That is not a calculation this can work out" instead.
        old_why = getattr(self, "_err_why", None)
        old_value = getattr(self, "_value", None)
        self.expr = expression
        self._x_bind = x
        try:
            result = self.evaluate()
            value, why = self._value, self._err_why
        finally:
            self.expr = old_expr
            self._x_bind = old_bind
            self._err_why = old_why
            self._value = old_value
        if result == "Error": raise ValueError(why)
        # The NUMBER, not the text: under Fix 2 every sample of a curve came
        # back rounded to two decimals and X^2/10 was drawn as a staircase.
        try:
            return float(result if value is None else value)
        except OverflowError:
            return float("inf")        # too big to plot; the sampler drops it

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
        self._draw_axis_numbers(cr, win, w, h)
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
        # The trace CURSOR. Left/Right moved a point the readout described and
        # nothing showed: "Y1 X=1 Y=0.017" under a graph with no mark on it.
        # A ring in the curve's own colour, hollow so the curve stays legible
        # through it, sits at the traced point when it is on screen; the label
        # below still says where when it is not.
        marker = self.trace_point()
        if marker is not None:
            px, py = graph_to_pixel(marker[0], marker[1], win, w, h)
            if -8 <= px <= w + 8 and -8 <= py <= h + 8:
                cr.set_source_rgb(*colors[self.trace_curve % 4])
                cr.set_line_width(2)
                cr.arc(px, py, 5, 0, 2 * math.pi)
                cr.stroke()
                cr.set_line_width(1)
        self._update_trace()
        return False

    def _clamp_trace(self):
        """Keep the trace inside the window it is drawn in.

        _on_graph_key clamps each arrow press for exactly this reason -- "a
        trace that cannot leave the graph cannot get lost on it" -- but the
        window moves too, and zooming it out from under the trace strands it
        just as thoroughly. MEASURED: click near the right edge of a standard
        window (X=8.07), press Zoom In, and the graph now runs -5..5 while the
        readout goes on saying "Y1 X=8.07228915663 Y=6.51618522282" for a point
        off the edge with no ring anywhere. trace_x is persisted, so it reopens
        just as lost. One rule, called from every place the window changes."""
        self.trace_x = min(self.window["xmax"],
                           max(self.window["xmin"], self.trace_x))

    def trace_point(self):
        """(x, y) of the trace on its curve, or None when the curve is off,
        empty, or has no value there. The same evaluation the readout uses."""
        i = self.trace_curve
        if not (0 <= i < len(self.ys)) or not self.y_enabled[i] \
                or not self.ys[i].strip():
            return None
        try:
            y = self._eval_x(self.ys[i], self.trace_x)
        except Exception:
            return None
        return (self.trace_x, y) if math.isfinite(y) else None

    @staticmethod
    def axis_number_step(lo, hi, scale, pixels, min_gap=44):
        """The gridline multiple that gets a number beside it: every `scale`
        when there is room, else every 2nd, 5th, 10th... so labels never
        crowd. Numbers went missing altogether: the grid was ruled at xscl and
        the axes drawn, and nothing said what a square was worth."""
        span = hi - lo
        if scale <= 0 or span <= 0 or pixels <= 0:
            return None
        per_line = pixels * scale / span
        if per_line >= min_gap:
            return scale
        need = min_gap / per_line
        for mult in (2, 5, 10, 20, 50, 100, 200, 500, 1000):
            if mult >= need:
                return scale * mult
        return None

    def _draw_axis_numbers(self, cr, win, w, h):
        cr.save()
        cr.set_source_rgb(0.42, 0.40, 0.36)
        cr.set_font_size(10)
        ox, oy = graph_to_pixel(0, 0, win, w, h)
        # keep the numbers on the canvas when the axis itself is off it
        ax_y = min(max(oy, 12), h - 4)
        ax_x = min(max(ox, 4), w - 30)
        for axis in ("x", "y"):
            lo, hi = win[axis + "min"], win[axis + "max"]
            step = self.axis_number_step(lo, hi, win[axis + "scl"],
                                         w if axis == "x" else h)
            if step is None:
                continue
            n = math.ceil(lo / step) * step
            guard = 0
            while n <= hi and guard < 400:
                guard += 1
                if abs(n) > step / 2:            # the origin is unlabelled
                    text = format_number(n)
                    ext = cr.text_extents(text)
                    # Kept WHOLE inside the canvas: centred on its gridline,
                    # the first and last numbers of a window ran off the edge
                    # and "-10" was drawn as "0" -- a label saying the wrong
                    # number is worse than one nudged a few pixels along.
                    if axis == "x":
                        px, _py = graph_to_pixel(n, 0, win, w, h)
                        tx = min(max(px - ext.width / 2, 2.0),
                                 max(2.0, w - ext.width - 2))
                        cr.move_to(tx, ax_y - 3)
                    else:
                        _px, py = graph_to_pixel(0, n, win, w, h)
                        ty = min(max(py + ext.height / 2, ext.height + 2.0),
                                 max(ext.height + 2.0, h - 2))
                        cr.move_to(ax_x + 4, ty)
                    cr.show_text(text)
                n += step
        cr.restore()

    def _on_graph_press(self, area, event):
        """A click on the canvas traces where it was clicked -- and, first,
        gives the canvas the keyboard.

        The canvas could take focus (set_can_focus) and answered arrow keys
        (_on_graph_key), but nothing ever gave it focus except a walk through
        thirteen Tab stops: it listened for no button at all. Measured on the
        module as it stood -- click the graph, press Right three times,
        trace_x is still 0.0 and the readout still says X=0. The trace, its
        readout and the ring drawn at the traced point were unreachable with a
        mouse. A canvas that answers keys has to take the click that aims
        them."""
        area.grab_focus()
        w = max(1, area.get_allocated_width())
        h = max(1, area.get_allocated_height())
        x, _y = pixel_to_graph(event.x, event.y, self.window, w, h)
        self.trace_x = x
        self._clamp_trace()
        self._update_trace()
        area.queue_draw()
        return True

    def _on_graph_key(self, _area, event):
        name = Gdk.keyval_name(event.keyval)
        enabled = [i for i, on in enumerate(self.y_enabled) if on and self.ys[i].strip()]
        if name in ("Left", "Right"):
            step = (self.window["xmax"] - self.window["xmin"]) / 100
            # CLAMPED to the window. Held down, this used to walk the trace
            # straight off the graph and keep going: 400 presses from x=0 in a
            # [-10, 10] window put it at x=80, pixel 4050 of a 900px canvas,
            # while the readout confidently reported "X=80 Y=0.984807753012"
            # for a point nobody could see. trace_x is persisted, so closing
            # the app did not recover it either -- it reopened just as lost.
            # A trace that cannot leave the graph cannot get lost on it.
            self.trace_x += (-1 if name == "Left" else 1) * step
            self._clamp_trace()
        elif name in ("Up", "Down") and enabled:
            try: p = enabled.index(self.trace_curve)
            except ValueError: p = 0
            self.trace_curve = enabled[(p + (-1 if name == "Up" else 1)) % len(enabled)]
        else: return False
        self._update_trace(); self.graph.queue_draw(); return True

    def _update_trace(self):
        # The SAME point the ring is drawn at, which is what makes the caption
        # and the picture one statement. Read straight from ys[] instead, the
        # readout described a curve that was not on the graph: untick every
        # function and the canvas is empty, no ring is drawn -- and the line
        # under it still read "Y1  X=8.07228915663  Y=6.51618522282", a value
        # for something nobody could see and nothing had plotted. trace_point()
        # already knows the rule ("None when the curve is off, empty, or has no
        # value there"); this is its other reader.
        point = self.trace_point()
        sy = (_t("Undefined") if point is None
              else format_number(point[1], self.fix))
        trace = _t("Y%d  X=%s  Y=%s") % (self.trace_curve + 1,
            format_number(self.trace_x, self.fix), sy)
        # Skipped when the readout already says this, the same way _apply_face
        # skips a key whose face is unchanged -- and here it matters more,
        # because this runs from inside _draw_graph. gtk_label_set_text queues
        # a resize whether or not the text differs, and a paint that writes to
        # a widget asks for the next paint. In a mapped toplevel the frame
        # clock absorbs that; in an offscreen holder (tools/appdrive.py, the
        # OS's own real-use driver) it does not, and the Graph page repainted
        # 601 times for one still picture -- 25s of CPU for a readout that
        # said "Y1  X=0  Y=0" every single time. Measured: with this write
        # suppressed the same drive takes ONE draw. A screenful of numbers
        # that has not changed should cost nothing to keep on screen.
        if trace == getattr(self, "_trace_txt", None):
            return
        self._trace_txt = trace
        self.trace_label.set_text(trace)
        try:
            acc = self.graph.get_accessible()
            acc.set_description(trace)
            acc.notify_visible_data_changed()
        except Exception:
            pass

    @staticmethod
    def _table_cell(text, header=False):
        """One cell of the table, RIGHT-aligned.

        Every cell used to be a plain Gtk.Label, which centres — so a column of
        numbers had its digits centred against each other and the decimal points
        wandered from row to row. `0.0174524064373` sat over `0.13917310096`
        over `0.5`, none of them lined up, and the column could not be read down.
        Right-aligning ends every value in a column at the same x, which is what
        makes a numeric column scannable. The header is aligned the same way so
        it sits over its own column rather than beside it."""
        lbl = Gtk.Label(label=text, xalign=1.0)
        lbl.get_style_context().add_class("tblhead" if header else "tblcell")
        return lbl

    def _refresh_table(self):
        for child in self.table_grid.get_children(): self.table_grid.remove(child)
        enabled = [(i, e) for i, e in enumerate(self.ys) if self.y_enabled[i] and e.strip()]
        headers = ["X"] + ["Y%d" % (i + 1) for i, _e in enabled]
        for c, label in enumerate(headers):
            self.table_grid.attach(self._table_cell(label, header=True), c, 0, 1, 1)
        for row in range(40):
            x = self.tbl_start + row * self.tbl_step
            self.table_grid.attach(self._table_cell(format_number(x, self.fix)),
                                   0, row + 1, 1, 1)
            for c, (_i, expression) in enumerate(enabled, 1):
                try: value = format_number(self._eval_x(expression, x), self.fix)
                except Exception: value = _t("Undefined")
                self.table_grid.attach(self._table_cell(value), c, row + 1, 1, 1)
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
                if vals:
                    lo, hi = min(vals), max(vals)
                    # A CONSTANT function makes lo == hi, and a window with no
                    # height divides by zero in graph_to_pixel on the very next
                    # draw. Measured: Y1=5, Zoom Fit, ZeroDivisionError inside
                    # the draw handler -- so the graph stopped painting
                    # entirely, from two keystrokes and a button. Give a flat
                    # line room to be a flat line.
                    if not hi > lo:
                        pad = max(1.0, abs(lo) * 0.1)
                        lo, hi = lo - pad, hi + pad
                    self.window["ymin"], self.window["ymax"] = lo, hi
            else:
                for a, b in (("xmin", "xmax"), ("ymin", "ymax")):
                    mid = (self.window[a] + self.window[b]) / 2; half = (self.window[b] - self.window[a]) * factor / 2
                    self.window[a], self.window[b] = mid - half, mid + half
        self._clamp_trace()
        self._update_trace()
        self.graph.queue_draw()

    def _window_dialog(self, *_):
        dialog = Gtk.Dialog(title=_t("Window"), transient_for=self, flags=Gtk.DialogFlags.MODAL)
        dialog.add_button(_t("Cancel"), Gtk.ResponseType.CANCEL); dialog.add_button(_t("Apply"), Gtk.ResponseType.OK)
        grid = Gtk.Grid(column_spacing=8, row_spacing=4); entries = {}
        for row, key in enumerate(("xmin", "xmax", "ymin", "ymax", "xscl", "yscl")):
            # Xmin / Xmax / ... — the label a graphing calculator puts on these,
            # rather than the dict key. Left untranslated on purpose, like the
            # sin/cos/log key faces: they are mathematical notation, not prose.
            grid.attach(Gtk.Label(label=key.capitalize()), 0, row, 1, 1)
            ent = Gtk.Entry(); ent.set_text(format_number(self.window[key], self.fix))
            ent.get_style_context().add_class("winfield")
            grid.attach(ent, 1, row, 1, 1); entries[key] = ent
        # What the dialog says when Apply cannot be honoured. Hidden until it
        # is needed; the same sentence sanitize_state's rules amount to.
        note = Gtk.Label(label=_t("Numbers only. Minimum below maximum, scale above zero."), xalign=0)
        note.set_line_wrap(True); note.set_max_width_chars(34)
        note.get_style_context().add_class("winnote")
        note.set_no_show_all(True)
        grid.attach(note, 0, 6, 2, 1)
        dialog.get_content_area().add(grid); dialog.show_all()
        # Apply used to close the dialog whatever was typed: Xmin 10 / Xmax
        # -10, or "abc", was read, found invalid and DROPPED, and the person
        # was left with the old graph and no idea their window was refused.
        # Now an invalid window keeps the dialog open, marks the fields it
        # cannot read, and says why; only Cancel or a good Apply closes it.
        while dialog.run() == Gtk.ResponseType.OK:
            values, bad = {}, []
            for k, e in entries.items():
                try:
                    values[k] = float(e.get_text())
                except ValueError:
                    bad.append(k)
            if not bad and window_is_valid(values):
                self._apply_window(values)
                break
            if not bad:
                # Every field reads as a number and they still do not make a
                # window: the pair (or scale) that is out of order.
                if not values["xmin"] < values["xmax"]: bad += ["xmin", "xmax"]
                if not values["ymin"] < values["ymax"]: bad += ["ymin", "ymax"]
                if not values["xscl"] > 0: bad.append("xscl")
                if not values["yscl"] > 0: bad.append("yscl")
                if not bad: bad = list(entries)       # nan/inf: nothing orders
            for k, e in entries.items():
                ctx = e.get_style_context()
                (ctx.add_class if k in bad else ctx.remove_class)("error")
            note.show()
        dialog.destroy()

    # ---- display ----
    def _apply_window(self, values):
        """Take a window the person typed. Lifted out of the dialog so it can
        be CHECKED: everything else in _window_dialog sits behind a
        `dialog.run()`, which no test can drive without a modal loop, and what
        Apply does to the TRACE is exactly the sort of thing that goes missing
        there (see _clamp_trace)."""
        self.window.update(values)
        self._clamp_trace()
        self._update_trace()
        self.graph.queue_draw()

    def _damage_strip(self):
        """The line above the readout that appears only when the stored
        calculator could not be read. Dismissible, because it is news rather
        than a state: once it has been read it has done its whole job, and a
        calculator that keeps a paragraph of explanation pinned over its keypad
        has been made worse by the fix.

        It sits INSIDE the paper card and above the display, so it reads as part
        of the same sheet -- the alternative, floating it over the readout,
        would cover the one thing the person opened the app to look at."""
        self.damage_rev = Gtk.Revealer()
        strip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        strip.get_style_context().add_class("damage-note")

        self.damage_lbl = Gtk.Label(label=_damaged_note(), xalign=0)
        self.damage_lbl.set_line_wrap(True)
        self.damage_lbl.set_max_width_chars(30)
        self.damage_lbl.get_style_context().add_class("damage-text")
        strip.pack_start(self.damage_lbl, True, True, 0)

        shut = Gtk.Button(label="\u00d7")
        shut.set_relief(Gtk.ReliefStyle.NONE)
        shut.set_valign(Gtk.Align.START)
        shut.set_tooltip_text(_t("Close"))
        shut.get_accessible().set_name(_t("Close"))
        shut.get_style_context().add_class("damage-shut")
        shut.connect("clicked", self._dismiss_damage)
        strip.pack_end(shut, False, False, 0)

        self.damage_rev.add(strip)
        return self.damage_rev

    def _dismiss_damage(self, *_):
        nbtransitions.reveal(self.damage_rev, False)

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
        # ...on the card's own paper. A ScrolledWindow and its viewport paint
        # the theme's own surface, which on Papertone is the DESK tone: the
        # running-history strip was a grey slab cut out of the middle of the
        # paper card, and on an empty calculator -- the first thing anyone
        # sees -- it was a grey slab with nothing in it. Every other surface
        # in this file paints its own tone for the same reason.
        tape_scroll.get_style_context().add_class("tape")
        tape_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        tape_scroll.set_size_request(
            -1, 68 if getattr(self, "_compact", False) else 92)
        self.tape_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.tape_box.set_direction(Gtk.TextDirection.LTR)   # expressions, not prose
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
        self.hist_lbl.set_direction(Gtk.TextDirection.LTR)
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
        # The readout is a NUMBER, and it must hug the same edge in every
        # language: digits are written left to right in Yiddish as in English,
        # and a figure grows leftward from the units column as it is typed.
        # Under the process-wide RTL direction the whole display flipped and the
        # answer sat against the left edge with the room to grow on the wrong
        # side. The instrument is pinned; the kicker labels above it are text
        # and stay with the language.
        self.disp_lbl.set_direction(Gtk.TextDirection.LTR)
        self.disp_lbl.set_hexpand(True)
        self.disp_lbl.set_ellipsize(Pango.EllipsizeMode.START)
        self.disp_lbl.set_max_width_chars(1)
        self.disp_lbl.get_style_context().add_class("disp-main")
        box.pack_start(self.disp_lbl, False, False, 0)
        return box

    @staticmethod
    def _tape_label(text, xalign):
        """One end of a tape row, bounded.

        THE BOUND IS THE POINT, and it was missing. A tape row was a plain
        Gtk.Label, which asks for the whole string as its natural width -- and
        the tape sits in a ScrolledWindow whose horizontal policy is NEVER, so
        that width goes straight into the card. MEASURED at 1024x740, adding up
        a receipt of 42 items (250 characters, the most the expression bound
        allows):

            card 640 -> 1690 px wide, on a 1024 px screen

        Everything past the fifth keypad column -- AC, backspace, the closing
        bracket, and every one of the operator keys including "=" -- was off the
        right-hand edge of the screen with no way to scroll to it, and it stayed
        that way, because AC clears the expression and not the tape. The
        calculator was unusable from then on, from nothing but a long sum.

        Both display labels above already carry the cure (see _display): an
        ellipsize mode plus a natural width capped to one character, so the
        label fills what it is given instead of demanding what it holds. A tape
        row needs exactly the same two lines. The expression ellipsizes at the
        END, where a row is read as the name of a calculation; the result keeps
        a real cap, wide enough for the longest answer this calculator can print
        (a 20-digit exact integer and its sign).
        """
        lbl = Gtk.Label(label=text, xalign=xalign)
        lbl.set_direction(Gtk.TextDirection.LTR)   # expressions, not prose
        if xalign:
            lbl.set_ellipsize(Pango.EllipsizeMode.START)
            lbl.set_max_width_chars(_TAPE_RESULT_CHARS)
        else:
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            lbl.set_max_width_chars(1)
        return lbl

    def _on_history_key(self, _box, event):
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            self.recall(-1)
            return True
        return False

    # ---- keypad ----
    def _keypad(self):
        """Two grids, and the split is the whole point.

        KEYS is written in six groups, and five of them are six keys wide and
        read as a calculator does — `sqrt pi 7 8 9 div`, `x2 e 4 5 6 x`,
        `1/x x! 1 2 3 -`, `+/- % 0 . = +`. The FIRST group is eight: the
        function keys. Laying all 38 out with `divmod(i, 6)` therefore shifted
        every row after the first by two, and the result on screen was 7 and 8
        marooned at the end of one row with 9 beginning the next, 4 and 5 ending
        the row after that, and a hole four cells wide under `=`. The digits
        were not in a number pad at all.

        Nothing was wrong with the LIST; it was being folded at the wrong width.
        So the eight function keys get their own eight-wide strip and the
        remaining thirty fold at six into exactly five full rows, which is why
        no key had to be added, moved or dropped to fix this and why there is
        now no empty cell anywhere in the pad.

        The two grids are separate widgets but one surface: same background,
        same 1px gaps, and the strip carries the pad's own bottom border so the
        seam between them reads as another key gap rather than a join."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        box.get_style_context().add_class("keypad")

        strip = Gtk.Grid()
        strip.get_style_context().add_class("keystrip")
        pad = Gtk.Grid()
        for g in (strip, pad):
            g.set_row_spacing(1)
            g.set_column_spacing(1)
            g.set_column_homogeneous(True)
            g.set_row_homogeneous(True)
            g.get_style_context().add_class("keypad")
            # A keypad is an INSTRUMENT, not a sentence. nbapp sets the process
            # default direction to RTL for Yiddish, and Gtk.Grid mirrors its
            # columns with it — which rendered the number block as 9 8 7 /
            # 6 5 4 / 3 2 1 / . 0. Digits are written left to right in Hebrew,
            # Yiddish and Arabic alike, and every calculator sold into those
            # markets has the standard Western pad; a mirrored one is not a
            # translation, it is a different machine. Pinned LTR. The chrome
            # around it — the view bar, the menus, the kicker labels — is text
            # and stays with the language.
            g.set_direction(Gtk.TextDirection.LTR)
        box.pack_start(strip, False, False, 0)
        box.pack_start(pad, True, True, 0)

        for i, kd in enumerate(KEYS):
            if i < STRIP_KEYS:
                grid, r, c = strip, 0, i
            else:
                grid = pad
                r, c = divmod(i - STRIP_KEYS, PAD_COLS)
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
            # ...and the KEY FACES with it. Pinning the grid stops the columns
            # mirroring but not the glyphs: under an RTL paragraph direction the
            # bidi algorithm draws "(" as ")" and vice versa, so the two bracket
            # keys swapped faces while still inserting what they always did —
            # the key that LOOKED like ")" typed "(". A label inherits the
            # direction it is realised under, so each face is pinned too.
            btn.set_direction(Gtk.TextDirection.LTR)
            child.set_direction(Gtk.TextDirection.LTR)
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
        return box

    # ---- logic ----
    def _on_press(self, _btn, kd):
        self.press(kd)

    def press(self, kd):
        label, action, value, ktype = kd
        # A key that EDITS dismisses a lingering error display; "=" re-raises
        # it below if the fresh expression is still invalid. The two MODE keys
        # (2nd, DEG) touch no expression, so the sentence stays until something
        # is typed: clearing it there left the failed "10÷0 =" over a
        # placeholder 0 -- exactly the false reading the "=" branch below
        # refuses to paint.
        was_error = self.error
        if action not in ("second", "deg"):
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
            previous = self.deg
            self.deg = not self.deg
            if not self._save_prefs():
                self.deg = previous
        elif action == "ac":
            self.expr = ""
            self.history = ""
            self.just_evaled = False
            self.second = False
        elif action == "back":
            self.expr = self.expr[:-1]
            self.just_evaled = False
        elif action in ("neg", "inv"):
            # Both of these WRAP whatever is on the display -- and straight
            # after "=" what is on the display may not be the answer. Under a
            # Fix mode it is a ROUNDING of the answer, so wrapping the text is
            # how a display setting becomes the arithmetic. MEASURED, Fix 2:
            #
            #     2÷3 =        0.67        (the answer is 0.666666666667)
            #     then 1/x =   1.49        (the answer is 1.5)
            #     then +/- =   -0.67, and Ans is now -0.67 too, so
            #                  ×3 = came back -2.01 instead of -2
            #
            # _continued_answer() is the same token the operator keys already
            # use for this: it hands back the display text when the display IS
            # the answer, and "Ans" -- the number itself -- when it is not.
            inner = (self._continued_answer()
                     if self.just_evaled and self.expr else self.expr)
            if action == "neg":
                self.expr = ("−(" + inner + ")") if inner else "−"
            elif inner:
                # Reciprocal wraps as 1÷(…). With nothing entered there is
                # nothing to invert, so this is a no-op rather than leaving a
                # dangling "1÷(" that can only ever evaluate to "Error".
                self.expr = "1÷(" + inner + ")"
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
                    self.ans = self._answer_value(r)
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
                if self.just_evaled:
                    # Carrying the answer on: it must be the ANSWER that
                    # carries on, not the rounding a display mode showed it as.
                    self.expr = self._continued_answer()
                if v == "(":
                    # "(" completes a function name typed letter by letter:
                    # the key ladder spelled it SIN, the keypad spells it sin(.
                    # Give the display the keypad's spelling (see
                    # _NAME_BY_LOWER; evaluate() reads either).
                    m = re.search(r"[A-Za-z]{2,}[0-9]*$", self.expr)
                    if m is not None and m.group(0).lower() in _NAME_BY_LOWER:
                        self.expr = (self.expr[:m.start()]
                                     + _NAME_BY_LOWER[m.group(0).lower()])
                self.expr = append_expression(self.expr, v)
            self.just_evaled = False
            self.second = False
        # Leaving the error state with nothing on the display (⌫ on the empty
        # display, a cancelled dialog): the failed line goes with it, or
        # "10÷0 =" sits over the placeholder 0 as though that were the answer.
        if was_error and not self.error and not self.expr:
            self.history = ""
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
        self._value = None
        return "Error"

    def _answer(self, value, text):
        """Return the TEXT a display shows, remembering the NUMBER it is.

        evaluate() answers with a string, and three callers needed the number:
        Ans, STO-> and the graph/table sampler all did float() on that string.
        So the Fix display mode -- a setting about how many decimals to SHOW --
        became the arithmetic. Measured on the module as it stood:

            Fix 2   2/3 = 0.67, then x3 =        2.01   (the answer is 2)
            Fix 0   1250/12 = 104, then x12 =    1248   (the answer is 1250)
            Fix 0   STO-> stored 104             (the value is 104.166666667)
            Fix 0   Y1=X^2/10 drew a STAIRCASE, every sample rounded to a whole

        The number is kept here beside the text, and every one of those callers
        reads the number. Float mode is unchanged: its text already IS the
        twelve-significant-figure value this calculator answers in."""
        self._value = value
        return text

    def _answer_value(self, text):
        """The number the last answer IS, for Ans / STO / a continued sum --
        never the text a display mode rounded it to. Falls back to reading the
        text when there is no number (an exact integer past what float can
        carry still reads as its own magnitude, exactly as it used to)."""
        for candidate in (getattr(self, "_value", None), text):
            if candidate is None:
                continue
            try:
                return float(candidate)
            except (TypeError, ValueError, OverflowError):
                continue
        return 0.0

    def _continued_answer(self):
        """The text that continues the answer on the display without CHANGING
        it. The display normally holds the answer itself, and then it just
        carries on. Under a Fix mode it holds a rounded rendering instead, and
        carrying that on is how 2/3 x3 came back 2.01 -- so the continuation
        becomes "Ans", which is the number itself and is the same token the
        empty-display operator rule already writes."""
        try:
            if float(self.expr) == float(self.ans):
                return self.expr
        except (TypeError, ValueError, OverflowError):
            pass
        return "Ans"

    def evaluate(self):
        # "Error" stays the sentinel the caller tests for; _err_why carries
        # the sentence the DISPLAY shows, so the person is told what went
        # wrong rather than only that something did. Reset on every attempt.
        self._err_why = None
        self._value = None
        js = self.expr
        if not js.strip():
            return self._answer(0.0, "0")
        js = (js.replace("×", "*").replace("÷", "/")
                .replace("−", "-").replace("π", "(PI)")
                .replace("√", "sqrt").replace("^", "**"))
        # A typed name arrives in whatever case the keyboard gave it (SIN(30),
        # ANS); read it as the name it spells. See _NAME_BY_LOWER.
        js = canonical_names(js)
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

        # RADIANS had the same disease from the other side: pi has no exact
        # binary form, so sin(π) came back 1.22464679915e-16, cos(π÷2) was
        # 6.12323399574e-17 and tan(π) -1.22464679915e-16 -- on the very key
        # that inserts π. Reduce to quarter turns the way turn() does to
        # quarter-circles: an argument within a hair (1e-12, relative) of a
        # multiple of π/2 IS that multiple, because nothing on this keypad can
        # name an angle any closer to π than π itself. Everything else is left
        # to math's own correctly-rounded answer, unchanged.
        def quarter(x):
            """(quadrant 0-3, True) at a multiple of π/2 radians."""
            q = x / (math.pi / 2)
            k = round(q)
            if abs(q - k) <= 1e-12 * max(1.0, abs(q)):
                return int(k) % 4, True
            return 0, False

        def rsin(x):
            k, exact = quarter(x)
            return (0.0, 1.0, 0.0, -1.0)[k] if exact else math.sin(x)

        def rcos(x):
            k, exact = quarter(x)
            return (1.0, 0.0, -1.0, 0.0)[k] if exact else math.cos(x)

        def rtan(x):
            k, exact = quarter(x)
            if not exact:
                return math.tan(x)
            if k in (1, 3):
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

        def count_args(n, r):
            """Validated, bounded whole-number arguments for nCr/nPr."""
            if n != int(n) or r != int(r):
                raise ValueError("count needs whole numbers")
            n, r = int(n), int(r)
            if not 0 <= r <= n:
                raise ValueError("count outside its domain")
            # The old formulas built n! before dividing it back down, with no
            # bound at all. A pasted nCr(100000000,2) therefore attempted a
            # hundred-million factorial and froze or exhausted the machine.
            # Match the explicit factorial key's established upper bound.
            if n > 10000:
                raise OverflowError("count too large")
            return n, r

        def ncr(n, r):
            n, r = count_args(n, r)
            return math.comb(n, r)

        def npr(n, r):
            n, r = count_args(n, r)
            return math.perm(n, r)

        env = {
            "sin": dsin if self.deg else rsin,
            "cos": dcos if self.deg else rcos,
            "tan": dtan if self.deg else rtan,
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
            "nCr": ncr,
            "nPr": npr,
            "random": random.random,
            "fact": fact,
            "_pow": _guarded_pow, "_add": _add, "_sub": _sub,
            "PI": math.pi,
            "e": math.e,
            "Ans": getattr(self, "ans", 0),
        }
        env.update({k: v for k, v in getattr(self, "variables", {}).items() if re.match(r"^[A-Z]$", k)})
        # The graph's sample point, when one is being plotted. Set last so it
        # shadows a stored variable of the same name -- which is what the old
        # textual substitution did too.
        if getattr(self, "_x_bind", None) is not None:
            env["X"] = self._x_bind
        try:
            # Rewrite  **  into bounded _pow() calls before evaluating so an
            # oversized power (9^9^9) is rejected up front instead of building a
            # giant integer that hangs/OOMs the app. A parse/syntax error on an
            # incomplete expression still degrades to "Error" as before.
            code = _CODE_CACHE.get(js)
            if code is None:
                tree = ast.parse(js, mode="eval")
                _ArithmeticGuard(env).visit(tree)
                _OpGuard().visit(tree)
                ast.fix_missing_locations(tree)
                code = compile(tree, "<calc>", "eval")
                if len(_CODE_CACHE) >= _CODE_CACHE_MAX:
                    _CODE_CACHE.clear()
                _CODE_CACHE[js] = code
            r = eval(code, {"__builtins__": {}}, env)  # noqa: S307
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
        fix = getattr(self, "fix", None)
        if isinstance(r, int) and not isinstance(r, bool):
            # ...unless a Fix mode is on: then 2+2 must read 4.00 beside the
            # 5.00 that 10÷2 already gave, or the chosen display mode is
            # applied to half the answers. Only an integer a float carries
            # exactly is handed to format_number; past that the digits ARE
            # the answer and stay whole.
            if fix is not None and abs(r) < 2 ** 53:
                return self._answer(r, format_number(r, fix))
            try:
                s = str(r)
            except (ValueError, MemoryError):
                # str() of an int with > 4300 digits raises on Python 3.11+
                return self._fail(_WHY_TOOBIG)
            return self._answer(
                r, s if len(s.lstrip("-")) <= _MAX_DIGITS else _sci(s))
        try:
            if math.isinf(r):
                return self._fail(_WHY_TOOBIG)
            if math.isnan(r):
                return self._fail(_WHY_NOANSWER)
            # match toPrecision(12) then trim
            r = float("%.12g" % r)
            if r == 0:
                r = 0.0          # never a "-0" / "-0.00" (0×−1, −sin(π))
            if r == int(r) and abs(r) < 1e16:
                return self._answer(
                    r, format_number(r, fix) if fix is not None else str(int(r)))
            return self._answer(
                r, format_number(r, fix) if fix is not None else repr(r))
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
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
                row.pack_start(self._tape_label(expression, 0), True, True, 0)
                row.pack_end(self._tape_label(result, 1), False, False, 0)
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
            available = bool(self.history)
            action = (_t("Click to use this calculation again")
                      if available else None)
            self._histbox.set_tooltip_text(action)
            self._histbox.set_can_focus(available)
            self._histbox.set_sensitive(available)
            self._histbox.get_accessible().set_name(action or "")
            if not available and self._histbox.has_focus():
                for _keydef, button, _label in self._buttons:
                    if button.get_sensitive():
                        button.grab_focus()
                        break
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
                # Both clipboard actions SHOW their key: Ctrl+C and Ctrl+V are
                # bound in _on_key_calc, and a shortcut nobody can discover is
                # not a feature (MENU-CONVENTIONS rule 3). Paste had no menu
                # entry at all, so the one way to bring an expression in was
                # a key the app never mentioned. Composed the way contacts.py
                # composes "Undo Delete Contact": the catalog carries the
                # words, the key is added after.
                (_t("Copy Result") + "    Ctrl+C", self._copy_result
                 if not getattr(self, "error", False) else None),
                nbcommands.item("edit.paste", self._paste_expression),
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
                # NO ELLIPSIS, and it is the same distinction the About box
                # makes. Function Catalog… offers a list to pick from and an
                # Insert button, so it asks before anything happens and earns
                # its ellipsis (rule 1). This one shows what is stored and
                # closes: one Close button, nothing to answer, nothing pending
                # on the answer. An ellipsis here promised a question the
                # dialog never puts, which is the promise rule 1 exists to
                # keep. Store Variable… is where a variable is actually named.
                (_t("Variables"), self._variables_dialog),
                nbapp.SEP,
                (deg_mark + _t("Degrees"), lambda: self._set_deg(True)),
                (rad_mark + _t("Radians"), lambda: self._set_deg(False)),
                # Named for what it DOES, with the ellipsis a dialog earns
                # (rule 1). Labelled with the current mode -- "Float", "Fix 2"
                # -- it read as a state, not an action, and promised nothing;
                # the current mode is already preselected inside the dialog.
                (_t("Display Mode") + "…", self._display_mode_dialog),
            ]
        return super().menu_items(name)

    def _copy_result(self):
        """Copy the current display value to the system clipboard, so a result
        can be pasted into another app (the keypad has no selectable text)."""
        # An error explanation is not a calculator result. Preserve whatever
        # useful value is already on the clipboard rather than replacing it
        # with translated UI prose such as "That cannot be calculated".
        if getattr(self, "error", False):
            return
        try:
            clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clip.set_text(self.disp_lbl.get_text(), -1)
            clip.store()
        except Exception:
            pass

    @staticmethod
    def _clipboard_expression(text):
        """Return a small, single-line calculator expression or ``None``.

        Clipboard text is outside input.  In particular, do not let terminal
        control characters, pasted prose, or a multi-megabyte selection become
        calculator state.  Names are limited to the vocabulary the keypad can
        produce (functions/constants plus the single-letter variable slots).
        """
        if not isinstance(text, str):
            return None
        # A number copied out of a document arrives with the space or the
        # newline that ended its line. Whitespace at the ends carries nothing
        # dangerous and the size cap is applied to what is left, so trim it
        # rather than refusing the paste in silence.
        text = text.strip()
        if not text or len(text) > MAX_EXPRESSION_CHARS:
            return None
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
            return None
        if not re.fullmatch(r"[0-9A-Za-z_+\-*/^().,!% ×÷π√]+", text):
            return None
        # The vocabulary is exactly the one the keypad and the catalog write
        # (_NAME_BY_LOWER, matched without regard to case the same way
        # canonical_names reads a typed name) plus the A-Z variable slots. The
        # hand-written list this replaces held thirteen names and the catalog
        # offers twenty-eight, so "floor(2.5)", "nCr(5,2)" and "log(100)*2"
        # were refused in silence -- expressions this calculator computes.
        names = re.findall(r"[A-Za-z_]+", text)
        if any(name.lower() not in _NAME_BY_LOWER
               and not re.fullmatch(r"[A-Za-z]", name) for name in names):
            return None
        return text

    def _paste_expression(self):
        """Request text from CLIPBOARD and replace the keypad expression."""
        try:
            clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)

            def received(_clipboard, text, *_unused):
                if self._closed:
                    return
                value = self._clipboard_expression(text)
                if value is None:
                    return
                # Spelled the way the keypad spells it, so a pasted "2*3" reads
                # 2×3 on a display whose own × key writes that character;
                # evaluate() reads either.
                self.expr = value.replace("*", "×").replace("/", "÷")
                self.error = False
                # ...and the display is now being TYPED, not carried on from
                # the last answer. Without this the paste landed on a display
                # still marked just_evaled, and the very next key threw it
                # away: after 7×7=, pasting "12" and pressing 5 left "5", and
                # pressing + left "Ans+" -- the pasted number gone, replaced by
                # the answer it was pasted over. Same two lines the keypad's own
                # press() sets for exactly the same reason.
                self.just_evaled = False
                self._tape_i = None
                self._refresh()

            clip.request_text(received)
        except Exception:
            pass

    def _set_deg(self, value):
        """Set degrees (True) / radians (False) explicitly from the View menu,
        persist it, and repaint the DEG key + mode line to match."""
        previous = self.deg
        self.deg = value
        if not self._save_prefs():
            self.deg = previous
        self._refresh()

    def _clear_all(self):
        self.press(("AC", "ac", None, "clear"))

    def _store_value(self):
        """The number STO-> should put into a variable, or None.

        This was `float(self.expr)` behind an `except ValueError: pass`, so
        storing worked only when the display already held a bare decimal and
        every other case did nothing AND SAID NOTHING. Measured, all silent:

            1+2       stored nothing      any unevaluated expression
            sqrt(9)   stored nothing      anything with a function
            2*PI      stored nothing      anything with a constant
            -5        stored nothing      <- the keypad's OWN minus key

        The last one is the one that makes this a real bug rather than a
        limitation: the minus key inserts U+2212 MINUS SIGN, which float() does
        not accept, so the calculator could not store a negative number typed on
        its own keypad. Nothing appeared, nothing was said, and the variable
        silently kept whatever it held before.

        Evaluated through the app's own evaluator now — the same one "=" uses,
        which is what every one of those cases needed. A genuinely unreadable
        expression still returns None, and evaluate() has already put the reason
        on the display by then.

        ...and evaluating the DISPLAY is exactly wrong for the one case the
        docstring above already names as fixed. Straight after "=" the display
        holds the answer as a display mode renders it, and re-reading that text
        stores the rendering. MEASURED, on the module as it stood:

            Fix 0   1250÷12 =  shows 104   STO-> B stored 104.0
                                           (the answer is 104.166666667)
            Fix 2   2÷3 =      shows 0.67  STO-> A stored 0.67
                                           (the answer is 0.666666666667)

        A memory register is a NUMBER, not a rendering of one, and the number
        is already kept beside the text (see _answer). Any edit at all clears
        just_evaled, so an expression the person is part-way through is still
        evaluated, exactly as before."""
        if self.just_evaled:
            value = _finite(getattr(self, "ans", 0), None)
            if value is not None:
                return value
        result = self.evaluate()
        if result == "Error":
            return None
        value = self._answer_value(result)
        return value if math.isfinite(value) else None

    def _store_dialog(self):
        dialog = Gtk.Dialog(title=_t("Store Variable"), transient_for=self, flags=Gtk.DialogFlags.MODAL)
        dialog.add_button(_t("Cancel"), Gtk.ResponseType.CANCEL); dialog.add_button(_t("Store"), Gtk.ResponseType.OK)
        entry = Gtk.Entry(); entry.set_max_length(1); entry.set_placeholder_text(_t("Letter A-Z"))
        # Store stays off until the field holds the letter the placeholder asks
        # for, rather than accepting the press and doing nothing with it.
        dialog.set_response_sensitive(Gtk.ResponseType.OK, False)
        entry.connect("changed", lambda e: dialog.set_response_sensitive(
            Gtk.ResponseType.OK, bool(re.match(r"^[A-Za-z]$", e.get_text()))))
        dialog.get_content_area().add(entry); dialog.show_all()
        name = entry.get_text() if dialog.run() == Gtk.ResponseType.OK else ""
        dialog.destroy()
        if name:
            self.store_from_display(name)

    def store_from_display(self, name):
        """STO-> the display into variable `name`. True when one was written.

        The failure used to be SILENT, and its docstring said otherwise: with
        "5+" on the display -- or "sqrt(" , or anything half-typed -- the
        dialog took the letter, _store_value() came back None, and the app did
        nothing and said nothing. The variable kept whatever it held. The
        reason evaluate() found is already the sentence the display shows a
        failed "=", so a refused store says the same thing in the same red."""
        if not re.match(r"^[A-Za-z]$", name):
            return False
        value = self._store_value()
        if value is None:
            self.error = True
            self._err_why = self._err_why or _WHY_UNREADABLE
            self._refresh()
            return False
        stored = self._store_variable(name.upper(), value)
        self._refresh()
        return stored

    def _store_variable(self, name, value):
        """Persist one memory register, rolling it back on write failure."""
        missing = object()
        previous = self.variables.get(name, missing)
        self.variables[name] = value
        if self._save_prefs():
            return True
        if previous is missing:
            self.variables.pop(name, None)
        else:
            self.variables[name] = previous
        return False

    def _variables_dialog(self):
        dialog = Gtk.Dialog(title=_t("Variables"), transient_for=self, flags=Gtk.DialogFlags.MODAL)
        dialog.add_button(_t("Close"), Gtk.ResponseType.CLOSE)
        # format_number, not str(): this listed variables in a DIFFERENT number
        # format from every other surface in the app. A stored 1 read "1.0"
        # here and "1" on the display, and storing 0.1+0.2 listed
        # "0.30000000000000004" while the display showed "0.3" -- the float
        # noise %.12g exists to hide. The Fix setting applies here too, for the
        # same reason: one display mode, everywhere.
        text = "\n".join("%s = %s" % (name, format_number(value, self.fix))
                         for name, value in sorted(self.variables.items())) \
            or _t("No Stored Variables")
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
        if dialog.run() == Gtk.ResponseType.OK:
            value = None if combo.get_active() == 0 else combo.get_active() - 1
            self._set_fix(value)
        dialog.destroy()

    def _set_fix(self, value):
        """Persist the display precision, restoring it on write failure."""
        previous = self.fix
        self.fix = value
        if self._save_prefs():
            self._refresh()
            return True
        self.fix = previous
        self._refresh()
        return False

    # ---- persistence ----
    # _load_prefs used to live here: a SECOND reader of calculator.json that
    # returned the angle mode and swallowed every error. Nothing called it --
    # __init__ has read the whole state through _load_state since the tape was
    # added. Deleted rather than left: it opened the same file with different
    # damage semantics (no quarantine, no notice, no _store_readable), so wiring
    # it back up for one small preference would have quietly restored the loss
    # this file's tests now guard against.

    def _load_state(self):
        """Read calculator.json, and make sure that whatever comes back, the
        bytes already on disk survive this session's first save.

        TWO damage classes, needing two different helpers, and this app called
        NEITHER. A file that fails to PARSE is nbapp.preserve_damaged's case. A
        file that parses into something that is not an object at all -- a JSON
        array, a bare string, the shape a bad merge or a sync tool that
        concatenates files produces -- is nbapp.quarantine_unrecognized's,
        because valid JSON of the wrong shape parses perfectly and only this app
        knows the shape is not a calculator.

        MEASURED, on the module as it stood: a store holding three variables and
        a tape, saved as a bare JSON string, was read as "nothing here"; the
        close-time flush wrote the blank default over it; preserve_damaged's
        .bak held the bytes for exactly one cycle, and the SECOND open+close
        overwrote that too. Two opens, two closes, no user action, no message.
        A wrong-shape ARRAY survived, which is what makes the bug easy to miss:
        _bak_would_shrink compares payload weight, and an array of the user's
        real keys outweighs the blank default while a bare string does not.

        `_store_readable` now means one narrow thing -- the original could NOT
        be moved out of harm's way -- because that is the only case where
        refusing to save is the lesser loss. It used to mean "the file did not
        parse", which gated every save for the whole session: the file was kept
        and everything the person did afterwards was silently discarded at
        close. contacts.py:494 records that exact cure shipping in journal and
        the save-failure gate catching it; this app still had it."""
        self._store_readable = True
        self._damaged = False        # did we fail to read what was there
        self._damaged_path = None    # where the bytes went, if we could move them
        try:
            data = _read_state_json()
        except FileNotFoundError:
            return {}
        except CalculatorStoreTooLarge:
            # Valid oversized JSON is not parse damage, so use the app-aware
            # mover; saving resumes only when the original is safely aside.
            self._damaged_path = nbapp.quarantine_unrecognized(STATE_FILE)
        except Exception:
            self._damaged_path = nbapp.preserve_damaged(STATE_FILE)
        else:
            if isinstance(data, dict):
                return data
            self._damaged_path = nbapp.quarantine_unrecognized(STATE_FILE)
        # Both branches land here: something was there and we could not read it.
        # Saving is safe once the original has been moved aside; when that move
        # FAILED the original is still sitting at STATE_FILE, and overwriting it
        # is the one outcome worth refusing.
        self._damaged = True
        self._store_readable = (self._damaged_path is not None
                                or not os.path.exists(STATE_FILE))
        return {}

    def _save_prefs(self):
        """Persist the angle mode to this app's private JSON file. Never crash
        on I/O — a read-only or missing config dir just skips the save."""
        try:
            # Only when the original could NOT be moved aside. A damaged store
            # that _load_state quarantined is already safe under its
            # .damaged-<stamp> name, so saving over the (now absent) file costs
            # nothing and the session's work is kept. Gating the save on "the
            # file was damaged" instead is the cure contacts.py:494 records
            # journal shipping: the file survived and everything the person did
            # afterwards was silently thrown away at close.
            if not self._store_readable:
                # ...and SAY so, once. Returning False here in silence made the
                # one state where NOTHING can be saved the one state that told
                # nobody: note_save_failure lives in the except branch below,
                # and this returns before it, so the notification centre was
                # never told either. Meanwhile the strip above the readout says
                # "A new one was started. The damaged file was kept." -- which
                # reads as "carry on" -- and everything the person works out
                # from then on is discarded at close with nothing anywhere
                # having said a word. MEASURED, with the config directory
                # unwritable and a damaged calculator.json inside it: the
                # original could not be moved aside, _store_readable went
                # False, _save_error stayed None and the tray stayed empty.
                # note_save_failure is once-per-owner, so a session's worth of
                # refused saves still leaves exactly one message.
                nbapp.note_save_failure(
                    self, OSError("the damaged store could not be moved aside"),
                    STATE_FILE)
                return False
            payload = dict(getattr(self, "_extra", {}) or {})
            payload.update({
                "deg": bool(self.deg), "fix": self.fix, "ans": self.ans,
                "tape": self.tape, "tape_results": self.tape_results,
                "variables": self.variables, "ys": self.ys,
                "y_enabled": self.y_enabled, "window": self.window,
                "tbl_start": self.tbl_start, "tbl_step": self.tbl_step,
                "trace_x": self.trace_x, "_extra": getattr(self, "_extra", {})})
            nbapp.atomic_write_json(STATE_FILE, payload)
            return True
        except Exception as exc:
            nbapp.note_save_failure(self, exc, STATE_FILE)
            return False

    def _on_destroy(self, *_):
        self._closed = True
        self._save_prefs()
        return False

    # ---- keyboard ----
    def _on_key(self, w, ev):
        # Esc LEAVES the page it is on before it leaves the window: on the
        # Graph or Table page it goes Home, and only from Home does it fall
        # through to the base handler and close the app. This has to be done
        # HERE, in the base's own hook: nbapp connects AppWindow._on_key before
        # __init__ can connect _on_key_calc, and the base returns True after
        # close(), so an Escape branch in _on_key_calc was never reached and
        # Esc on the graph quit the whole calculator (contacts.py takes Esc
        # the same way). An open menu / About card is dismissed by the base
        # first.
        if (ev.keyval == Gdk.KEY_Escape
                and getattr(self, "current_view", "home") != "home"
                and self._menu_open is None
                and getattr(self, "_about_layer", None) is None):
            self._switch_view("home")
            return True
        return super()._on_key(w, ev)

    def _on_key_calc(self, _w, ev):
        kv = ev.keyval
        name = Gdk.keyval_name(kv)
        editing_text = isinstance(self.get_focus(), (Gtk.Editable, Gtk.TextView))
        # Ctrl+C copies the current result — the same action as Edit ▸ Copy
        # Result, except while a real formula field owns normal text editing.
        if (ev.state & Gdk.ModifierType.CONTROL_MASK) and name in ("c", "C"):
            if editing_text:
                return False
            self._copy_result()
            return True
        if (ev.state & Gdk.ModifierType.CONTROL_MASK) and name in ("v", "V"):
            if editing_text:
                return False
            self._paste_expression()
            return True
        if ev.state & Gdk.ModifierType.CONTROL_MASK:
            if name in ("1", "2", "3"):
                self._switch_view({"1": "home", "2": "graph", "3": "table"}[name])
                return True
            if name in ("m", "M"):
                self._catalog_dialog(); return True
            if name in ("s", "S"):
                self._store_dialog(); return True
        # An unowned shortcut belongs to GTK/the desktop, not to the
        # expression editor.  Without this boundary Ctrl+A and Ctrl+Z fell
        # through to the single-letter variable rule below and silently typed
        # A or Z; Alt+letter did the same while a menu mnemonic was being used.
        # Leave these chords unclaimed so their normal window-wide action can
        # run.  Shift is deliberately not included: it is how symbols such as
        # +, %, ^ and parentheses reach the key-name table below.
        shortcut_mods = (Gdk.ModifierType.CONTROL_MASK
                         | Gdk.ModifierType.MOD1_MASK
                         | Gdk.ModifierType.SUPER_MASK)
        if ev.state & shortcut_mods:
            return False
        # Typing belongs to a focused text field. The graph view's Y1-Y4
        # expression boxes are real entries in this same toplevel, and this
        # handler runs before they see a single key — without this guard
        # every digit, letter and operator drove the keypad, Return
        # evaluated, BackSpace edited the EXPRESSION and Delete fired AC
        # while the user was correcting a typo in a formula box. Ctrl
        # chords and Escape stay above: their meanings are window-wide
        # (copy result, switch view, leave).
        if editing_text:
            return False
        # ...and on the Graph and Table pages the keypad is not on screen at
        # all. This handler runs at the WINDOW, before the focused widget sees
        # the key, so on the graph it swallowed Up/Down for the tape recall
        # (and Return, Delete, every digit for a display nobody could see):
        # the traced curve never changed and the Home display did. Decline
        # everything below so the arrows reach _on_graph_key; the Ctrl chords
        # above keep their window-wide meaning.
        if getattr(self, "current_view", "home") != "home":
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
            # The one place it stays a comma is inside nCr( / nPr( / nthRoot(,
            # which have two arguments and no other way to separate them --
            # see wants_argument, applied where this table is read.
            "comma": ",", "KP_Separator": ",",
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
            value = table[name]
            if value == ",":
                # ...unless a two-argument call is waiting for its separator.
                value = "," if wants_argument(self.expr) else "."
            self._press_value(value)
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
        """A keyboard key or a catalog pick, given the SAME key type its
        keypad twin has, because press() reads the type: after "=" a value
        that is not an operator starts a new expression, an operator continues
        the answer. Every non-digit used to be classed an operator, so typing
        A after 7×7= gave "49A" -- and, with A and B stored, A+B came back
        245 -- while the pad's own π key correctly began afresh."""
        if v.isdigit() or v == ".":
            ktype = "num"
        elif v in ("+", "−", "×", "÷"):
            ktype = "op"
        else:
            ktype = "fn"
        self.press((v, "app", v, ktype))

    # ---- css ----
    def _install_css(self):
        css = b"""
        /* The root owns the whole toplevel allocation, including the flanks
           beyond the centred card; keep those pixels opaque on framebuffer /
           no-compositor sessions, where an unpainted surface appears black. */
        .calcroot { background: #F8F7F2; }
        /* Desk tone shared OS-wide (calendar/g2048/illustrator). The scroller
           and its viewport paint the same OPAQUE desk so an over-scrolled or
           short panel never exposes a transparent (black) window surface. */
        .calcscroll, .calcscroll viewport { background: #DED4C2; }
        .calcstage { background: #DED4C2; padding: 40px; }
        .calcnav { background: #F1EEE6; border-bottom: 1px solid #C9C4B6; }
        .calcnav button { border-radius: 0; min-height: 34px; }
        .calcnav button.active { background: #C8341E; color: #FCFBF8; }
        .graphpage, .tablepage { background: #F8F7F2; padding: 14px; }
        /* The Window dialog's refused fields and its one-line note, in the
           signage red the display's own error state uses. */
        entry.winfield.error { border-color: #C8341E; color: #C8341E; }
        .winnote { color: #C8341E; font-size: 12px; margin-top: 6px; }
        /* A column of numbers is read DOWN, so the header carries the weight
           and the rule, and the values line up under it. */
        /* ...and every column is the same width, so the rule under a header
           spans its whole column instead of stopping at the width of the word
           "X", and a one-character column is not a quarter the width of the
           numbers under it. */
        .tblhead, .tblcell { min-width: 108px; padding-right: 14px; }
        .tblhead { font-weight: 600; color: #6E695E; font-size: 12px;
                   letter-spacing: 0.06em; padding-bottom: 4px;
                   border-bottom: 1px solid #C9C4B6; }
        .tblcell { font-size: 13px; color: #1A1916; }
        .calccard { background: #F8F7F2; border: 1px solid #1A1916;
                    box-shadow: 4px 4px 0 rgba(26,25,22,0.12); }
        .calccard * { font-family: "Nimbus Sans","Helvetica",sans-serif; }

        /* The damaged-store notice. It is on the card's own paper with a rule
           under it, not a coloured alert box floated over the readout: this is
           a line of the same sheet that happens to be about the sheet. The
           accent bar on the leading edge is the only colour, and it is the
           OS-wide warning tone rather than a new one. */
        .damage-note { background: #F1EEE6; padding: 12px 14px 12px 12px;
                       border-bottom: 1px solid #1A1916;
                       border-left: 4px solid #C8341E; }
        .damage-text { font-size: 12px; color: #3A382F; }
        /* A button's colour never reaches its own label without this: the label
           is a separate node, so `.damage-shut { color: }` styles the button
           box only and the glyph stays default-coloured. */
        .damage-shut { min-width: 22px; min-height: 22px; padding: 0;
                       border-radius: 0; color: #6E695E; }
        .damage-shut label { color: inherit; font-size: 15px; }

        .display { padding: 30px 28px 26px; border-bottom: 1px solid #1A1916; }

        /* Short panels (1024x768 and 1280x800). Same layout, tighter metrics:
           the whole card has to sit above the fold, because a calculator whose
           "=" needs a scroll is not a calculator. */
        .compact .calcstage { padding: 14px; }
        .compact .display { padding: 18px 24px 16px; }
        .compact .disp-hist { margin-top: 8px; min-height: 18px; }
        .compact .disp-main { font-size: 44px; min-height: 50px; }
        .compact .key { min-height: 52px; }
        .compact .keystrip .key { min-height: 44px; }
        .compact .damage-note { padding: 9px 12px 9px 10px; }
        .disp-kicker { font-size: 11px; letter-spacing: 0.16em;
                       color: #6E695E; font-weight: 600; }
        .disp-mode { font-size: 12px; letter-spacing: 0.08em; color: #6E695E;
                     font-weight: 600; }
        /* the tape strip and the history line's click target both paint the
           card's own paper: never leave a bare EventBox (or a ScrolledWindow,
           which paints the theme's desk tone) showing through the sheet */
        .tape, .tape viewport { background: #F8F7F2; }
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
        /* The function strip packs eight keys into the width the pad gives six,
           so its keys are shorter and their labels a step down -- a strip key is
           a destination, not something anyone drums on. */
        .keystrip .key { min-height: 52px; }
        .keystrip .key label { font-size: 15px; }
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
