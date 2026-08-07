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


# key defs: (label, action, value, type)
#   type -> num / op / eq / clear / fn
KEYS = [
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
        self.tape = []
        self._tape_i = None       # position while walking it (None = not)
        self._tape_draft = ""     # what was on the display before walking
        self.deg = self._load_prefs()
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
        self.content.pack_start(scroller, True, True, 0)

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

        self.connect("key-press-event", self._on_key_calc)
        self.connect("destroy", self._on_destroy)
        self._refresh()

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
                self._remember(prev)
                r = self.evaluate()
                if r == "Error":
                    # Keep what was tried visible on the history line and show a
                    # clear, honest "Error" in the main display — never a silent
                    # "0" that reads as though the bad input equalled zero.
                    self.history = prev.strip() + " ="
                    self.expr = ""
                    self.error = True
                    self.just_evaled = False
                else:
                    self.history = prev + " ="
                    self.expr = r
                    self.just_evaled = True
                self.second = False
        elif action == "app":
            v = ALT_VALUE[value] if (self.second and value in ALT_VALUE) else value
            is_op = ktype == "op" or v in ("^", "%", "^2", "!", ")")
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
    _TAPE_MAX = 30

    def _remember(self, expr):
        """File an expression that was worked out, newest last. A repeat of the
        one already on top is not stored twice — pressing = on the same sum
        again should not fill the history with copies of it."""
        expr = expr.strip()
        if not expr or (self.tape and self.tape[-1] == expr):
            return
        self.tape.append(expr)
        del self.tape[:-self._TAPE_MAX]

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
            "ln": math.log,
            "log": math.log10,
            "sqrt": math.sqrt,
            "fact": fact,
            "_pow": _guarded_pow,
            "PI": math.pi,
            "e": math.e,
        }
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
                return str(int(r))
            return repr(r)
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
                (deg_mark + _t("Degrees"), lambda: self._set_deg(True)),
                (rad_mark + _t("Radians"), lambda: self._set_deg(False)),
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

    def _save_prefs(self):
        """Persist the angle mode to this app's private JSON file. Never crash
        on I/O — a read-only or missing config dir just skips the save."""
        try:
            nbapp.atomic_write_json(STATE_FILE, {"deg": bool(self.deg)})
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
