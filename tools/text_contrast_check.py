#!/usr/bin/env python3
"""
text_contrast_check -- can a person actually READ every word in the OS?

WHY THIS EXISTS. tools/button_contrast_check.py measures real rendered labels,
which is the right method, but it only ever looks INSIDE a Gtk.Button. That is
a few percent of the words on the screen. Everything else -- a row's caption, a
tree cell, an eyebrow, a chip, a section header, the text in an entry -- was
invisible to every gate in this tree, so the OS accumulated an entire tier of
text that is too faint to read and nothing went red.

The measured example that started this: @muted-2 (#9A9484), whose ONE job in
tools/design_tokens.py is "placeholder text, disabled marks", is 2.92:1 on
@paper. It is used as ordinary readable body-adjacent text in dozens of places
-- novel's word counts and part headings, cookbook's ingredient amounts, music,
packages, settings. 2.92:1 does not clear the bar for large text, let alone for
the 11-12px it is usually set at. None of it was visible to a gate, because
none of it is inside a button.

WHAT IT DOES. It constructs every app, in its OWN process, and measures the
foreground and background GTK actually computes for every text node it can
reach:

    A. RESTING     every Gtk.Label (including each differently-coloured run of
                   Pango markup inside one), Gtk.Entry text, Gtk.Entry
                   placeholder, Gtk.TextView text, and every Gtk.TreeView cell
                   -- per column, per row, with the model's own per-row
                   foreground applied via cell_set_cell_data(), plus column
                   headers, menu items reached through get_submenu(), notebook
                   tab labels, header bars and MenuButton popovers.

    B. REACHED FOR the same nodes again with the interaction state really SET
                   on the widget that takes it, so hover and selection grounds
                   are measured rather than assumed. This is where the design
                   is at its most fragile: @muted on @paper is 5.28:1 and fine,
                   and the same text on @select (#EAE3D2) is 4.27:1. Text that
                   is faintest exactly when you reach for it is the defect this
                   pass exists for.

    C. DECLARED    a rendered probe for every colour rule in the app's own
                   stylesheet that pass A never reached -- the row that only
                   exists once there is data in it, the ".sel" class only added
                   on click. The probe is a REAL widget tree carrying the real
                   classes, so the colour is still the one GTK computes, not the
                   one the CSS source says; only the GROUND is assumed, and it
                   is assumed in the direction that cannot manufacture a defect
                   (see _declared_ground).

The point of C is that a gate must be honest about its own blind spot. A check
that silently scores 100% because it could not reach the failing node is worse
than no check: it is a green light nobody earned.

USAGE
    tools/guestrun.sh python3 tools/text_contrast_check.py
    tools/guestrun.sh python3 tools/text_contrast_check.py novel packages
    tools/guestrun.sh python3 tools/text_contrast_check.py --selfcheck
    tools/guestrun.sh python3 tools/text_contrast_check.py --one novel   # in-process

Colour maths, and the definition of "the surface this text is read against",
are IMPORTED from button_contrast_check rather than rewritten. Two tools that
disagree by a rounding step about what 4.5:1 means are two tools nobody can act
on.
"""
import os
import re
import subprocess
import sys

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, GLib, GObject, Pango  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
DE = os.environ.get("NB_TC_DE") or os.path.join(
    _REPO, "buildroot", "board", "notebookos", "rootfs-overlay", "opt",
    "notebook", "de")
sys.path.insert(0, DE)
os.environ.setdefault("NB_HOME", "/tmp/nbhome-textcontrast")
os.makedirs(os.environ["NB_HOME"], exist_ok=True)

sys.path.insert(0, _HERE)
import uishot                                                     # noqa: E402
# THE measuring machinery. ratio() is the WCAG relative-luminance formula and
# _effective_bg() is the walk that decides which surface a glyph is read
# against -- both already argued out, at length, in that file's docstrings.
import button_contrast_check as bcc                               # noqa: E402
from button_contrast_check import ratio, _rgba, _effective_bg, _lum  # noqa: E402
# button_contrast_check puts the REAL de/ on sys.path at position 0 when it is
# imported. That silently beat NB_TC_DE and made --selfcheck import the
# untouched app out of the tree while believing it had loaded the sabotaged
# copy -- the gate "stayed green on unreadable text" because it never saw the
# unreadable text. Re-assert our own tree after the import.
if sys.path[0] != DE:
    sys.path.insert(0, DE)

# STAND CLEAR OF THE SINGLE-INSTANCE LOCK. nbapp.claim_single_instance() calls
# os._exit(0) -- no traceback, no output, exit status ZERO -- when the app being
# constructed is already open in another process. This gate constructs 43 apps
# in 43 subprocesses on a developer desktop that may well have one of them open,
# and the symptom would be a subprocess that printed nothing and succeeded,
# which the parent reads as "no verdict". Pointing the registry at our own
# directory is the same guard construct_all_host.py carries, for the same
# reason: this process is not a real app and must never stand down.
try:
    import nbapp as _nbapp                                        # noqa: E402
    _nbapp._APP_DIR = os.path.join(os.environ["NB_HOME"], "nb-apps-textcontrast")
    os.makedirs(_nbapp._APP_DIR, exist_ok=True)
except Exception:                                                 # noqa: BLE001
    pass

# --------------------------------------------------------------- THE BARS
#
# Six, because a string on the screen can have six genuinely different jobs,
# and one bar for all of them either lets faint body text through or forbids
# the OS from having a quiet tier at all. Every one is stated here with what
# it is for and what it costs. Two of them are DEPARTURES from a literal
# reading of WCAG and both say so in as many words: a bar you cannot see being
# relaxed is a bar that has been quietly abandoned.
#
# 4.5:1  TEXT A PERSON MUST READ, at normal size.
#        WCAG 2.1 AA (1.4.3). Not an aspiration here: the display budget for
#        this OS is 1024x740 (see the small-screen work), the interface type
#        ramp bottoms out at 11px, and the grounds are warm papers rather than
#        white, which costs a little contrast at every step. This is the number
#        the ink ramp was BUILT to clear -- @muted (#6E695E) is 5.28:1 on
#        @paper and 4.55:1 on @hover, i.e. the palette's quiet tier already
#        passes. Nothing about hitting 4.5 requires flattening the hierarchy;
#        it requires using the rung of the ramp that was made for this.
#
# 3.0:1  LARGE OR HEAVY TEXT: >= 18pt (24px) at any weight, or >= 14pt
#        (18.7px) at weight >= 700.
#        WCAG 2.1 AA large-text exception, verbatim. Bigger glyphs put more ink
#        on the page per stroke, so the same ratio reads as more contrast.
#
# 3.0:1  A GENUINE PLACEHOLDER, or a DISABLED mark that still has to be seen.
#        This is a DELIBERATE DEPARTURE from WCAG, stated rather than smuggled:
#        1.4.3 exempts only "incidental" text and inactive components, and a
#        placeholder is neither -- strictly, placeholder text owes 4.5:1 too.
#        We do not hold it there, for one reason that survives argument: a
#        placeholder's job is to read as NOT-YET-CONTENT. Set it at body
#        contrast and an empty field looks filled, which costs every user
#        something real to buy a contrast number for text that vanishes the
#        moment it matters and is, in this OS, always duplicated by a visible
#        field label. 3.0:1 is the floor we will not go under: it is WCAG's own
#        bar for a UI component's boundary (1.4.11), i.e. the level at which a
#        thing is reliably PERCEIVABLE even if not comfortably readable.
#        @muted-2 at 2.92:1 on paper does not clear even this -- which is the
#        finding, not a reason to lower the bar.
#
# 3.0:1  A MARK SET IN TEXT: a disclosure caret, a saved-state dot, a multiply
#        sign standing in for a close box. Detected mechanically -- the string
#        contains no alphanumeric character at all -- rather than by a class
#        name, so nothing can opt into it by being called ".icon". These are
#        user-interface COMPONENTS that happen to be drawn with a font, and
#        WCAG grades them under 1.4.11 (non-text contrast, 3:1), not 1.4.3.
#        Holding a 11px caret to 4.5:1 would darken every affordance in the OS
#        to ink and destroy the hierarchy that tells a person which glyph is
#        the content and which is the furniture.
#
# 1.5:1  A MARK THAT ITS OWN LABEL ALSO SPELLS OUT. The saved-state dot in
#        `<span foreground="#7FA98C">* </span>Saved 18:13` carries no
#        information the same label does not state in words two characters
#        later. WCAG 1.4.11 applies to graphics "required to understand the
#        content", and this one is not: switch it off entirely and the label
#        still reads Saved. Narrow on purpose -- the rule is that the mark is
#        a markup RUN inside a label whose OTHER runs contain letters or
#        digits. A caret alone in its own label is not covered by it and is
#        still held to 3.0:1. Kept above the 1.5 floor, because a dot nobody
#        can see is still a dot that should not have been drawn.
#
# 1.5:1  NOT ON THE SCREEN. Inherited from button_contrast_check. An
#        insensitive control's text is exempt from the bars above (low contrast
#        is HOW disabled reads as disabled), but not from this one: a disabled
#        label a person cannot see at all is a defect in any theme, for any
#        reader, and is how you ship a menu with holes in it.
BAR_BODY = 4.5
BAR_LARGE = 3.0
BAR_PLACEHOLDER = 3.0
BAR_MARK = 3.0
BAR_INVISIBLE = bcc.INVISIBLE          # 1.5

# WCAG's own large-text definition, in points.
LARGE_PT = 18.0
LARGE_BOLD_PT = 14.0

GRADE_BODY = "text"
GRADE_LARGE = "large"
GRADE_PLACEHOLDER = "placeholder"
GRADE_DISABLED = "disabled"
GRADE_MARK = "mark"
GRADE_MARK_DUP = "mark-dup"

BARS = {GRADE_BODY: BAR_BODY, GRADE_LARGE: BAR_LARGE,
        GRADE_PLACEHOLDER: BAR_PLACEHOLDER, GRADE_DISABLED: BAR_INVISIBLE,
        GRADE_MARK: BAR_MARK, GRADE_MARK_DUP: BAR_INVISIBLE}

# ------------------------------------------------------- placeholder evidence
#
# WHAT COUNTS AS A PLACEHOLDER. Only two things:
#
#   1. text a widget itself declares to be one -- Gtk.Entry.get_placeholder_text
#      (GTK3 draws it in the entry's INSENSITIVE colour, which is where the
#      colour is read from), and
#   2. a node named in the table below.
#
# It is deliberately NOT inferred from the class name. ".hint", ".ph",
# ".placeholder" are claims, and a gate that accepts a claim can be silenced by
# a rename -- which is the exact shape of the "instrument reports, not code"
# failure this tool-set keeps rediscovering. novel's ".nvplaceholder" IS a real
# placeholder (the prompt drawn in an empty manuscript, gone the instant you
# type); an app's ".hint" that explains a setting underneath a switch is not,
# no matter what it is called, because a person has to read it to use the
# setting. Each entry below is a judgement someone made after looking at what
# the node does, written down once.
PLACEHOLDER_NODES = {
    # (module, css class): the LINE OF CODE that proves it, not a description
    # of what the name suggests. Two entries, because two is how many were
    # found by reading; a third was nearly added on the strength of the name
    # alone and the class did not exist.
    ("novel", "nvplaceholder"):
        "novel.py:1185 self.placeholder.set_visible(cur == 0) -- the label is "
        "on the page iff the chapter has no words in it",
    ("cookbook", "placeholder"):
        "cookbook.py adds it only in the else-branch of `if text:` and sets "
        "the label to entry.get_placeholder_text(); the text view's show_ph/"
        "hide_ph pair does the same on focus. It is on iff the field is empty",
}

# The same discipline for DISABLED. Pass A knows a control is insensitive by
# asking it; pass C, which builds a probe for a class no live node was carrying,
# cannot. Some apps mark a whole section unavailable with a class applied in the
# SAME statement as set_sensitive(False) -- and the greying IS the disabled
# state, which WCAG 1.4.3 exempts. Listed here with the line that proves it,
# because "it is called .dim" is not proof of anything.
DISABLED_NODES = {
    ("comics", "dim"):
        "comics.py _sync_tool: group.set_sensitive(on) and the class are set "
        "on adjacent lines, so .dim is only ever on an insensitive group",
    ("illustrator", "dim"):
        "illustrator.py: the helper that adds .dim calls "
        "widget.set_sensitive(on) two lines later; .dim IS the off state",
}

# --------------------------------------------------------------- the app list
#
# DERIVED, not typed. finder.APP_MODULES minus HIDDEN_APPS is the set a person
# can actually double-click; the hidden ones still ship in the tree and are
# still read by whoever opens them, so they are appended rather than dropped;
# and the session-start windows (panel, board, login) contain as much readable
# text as an app does. A hardcoded list is how language/maps/gbasdk went
# untested for a month while the summary line read like full coverage.
SESSION = ["finder", "shell", "widgets", "desktopbg", "widgetsettings",
           "login", "firstrun", "osk", "splash"]


def app_list():
    import finder
    apps = sorted({m for n, m in finder.APP_MODULES.items()
                   if n not in finder.HIDDEN_APPS})
    hidden = sorted({m for n, m in finder.APP_MODULES.items()
                     if n in finder.HIDDEN_APPS})
    out = []
    for n in apps + hidden + SESSION:
        if n not in out and os.path.exists(os.path.join(DE, n + ".py")):
            out.append(n)
    return out


# ------------------------------------------------------------------ findings
class Finding(object):
    __slots__ = ("app", "grade", "ratio", "fg", "bg", "text", "where",
                 "state", "pt", "weight", "source")

    def __init__(self, app, grade, r, fg, bg, text, where, state, pt, weight,
                 source):
        self.app, self.grade, self.ratio = app, grade, r
        self.fg, self.bg, self.text, self.where = fg, bg, text, where
        self.state, self.pt, self.weight, self.source = state, pt, weight, source

    @property
    def key(self):
        return (self.app, self.grade, self.fg, self.bg, self.where,
                self.state, self.source, round(self.pt))


def _hex(rgb):
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(round(x)))) for x in rgb)


def _font(ctx, state=Gtk.StateFlags.NORMAL):
    """(points, weight) as GTK resolved them for this node."""
    fd = ctx.get_property("font", state)
    size = fd.get_size()
    if fd.get_size_is_absolute():
        pt = (size / Pango.SCALE) * 0.75          # CSS px -> pt
    else:
        pt = size / Pango.SCALE
    try:
        weight = int(fd.get_weight())
    except Exception:                                             # noqa: BLE001
        weight = 400
    return pt, weight


def grade_of(pt, weight, placeholder=False, disabled=False, text="",
             spelled_out=False):
    if disabled:
        return GRADE_DISABLED
    if placeholder:
        return GRADE_PLACEHOLDER
    if text and not any(ch.isalnum() for ch in text):
        return GRADE_MARK_DUP if spelled_out else GRADE_MARK
    if pt >= LARGE_PT or (weight >= 700 and pt >= LARGE_BOLD_PT):
        return GRADE_LARGE
    return GRADE_BODY


def _where(w):
    """A short, greppable identity for the node: type + its style classes."""
    cls = " ".join(w.get_style_context().list_classes())
    name = type(w).__name__.replace("Gtk", "").lower()
    return ("%s.%s" % (name, cls.replace(" ", "."))) if cls else name


def _sample(text):
    t = " ".join((text or "").split())
    return t[:34]


# ---------------------------------------------------------------- traversal
def _edges(w):
    """Children a plain get_children() walk does NOT reach.

    Every one of these was a real hole: a menu bar's entire contents hang off
    get_submenu(), a notebook's tab labels are not children of anything, and a
    window's header bar is reached only through get_titlebar(). Missing them is
    how a gate reports "0 defects" on a window whose menus are unreadable."""
    out = []
    try:
        if isinstance(w, Gtk.MenuItem):
            sub = w.get_submenu()
            if sub is not None:
                out.append(sub)
        if isinstance(w, Gtk.MenuButton):
            for getter in ("get_popover", "get_popup"):
                try:
                    p = getattr(w, getter)()
                except Exception:                                 # noqa: BLE001
                    p = None
                if p is not None:
                    out.append(p)
        if isinstance(w, Gtk.Notebook):
            for child in w.get_children():
                lab = w.get_tab_label(child)
                if lab is not None:
                    out.append(lab)
        if isinstance(w, Gtk.Window):
            t = w.get_titlebar()
            if t is not None:
                out.append(t)
        if isinstance(w, Gtk.Frame):
            lab = w.get_label_widget()
            if lab is not None:
                out.append(lab)
        if isinstance(w, Gtk.TreeView):
            for col in w.get_columns():
                b = col.get_button()
                if b is not None:
                    out.append(b)
    except Exception:                                             # noqa: BLE001
        pass
    return out


def walk(root):
    """Every widget in the tree, once, including the edges above.

    Returns a LIST, and that is load-bearing. The first version was a
    generator carrying a set of id()s for cycle-breaking, which is wrong in a
    way that is invisible until you count: PyGObject frees the Python wrapper
    for a widget as soon as the last reference goes, and the next wrapper
    allocated can land on the same address. A live widget then tested as
    ALREADY SEEN and its whole subtree was skipped. Measured on novel.py: 23
    nodes reached instead of several hundred -- the entire editor and chapter
    list silently absent -- and it varied run to run, so the gate reported a
    different number of defects each time it was asked. Holding a real
    reference to every node for the duration is what makes id() mean what the
    code assumed it meant all along."""
    out = []
    seen = set()
    stack = [(root, 0)]
    while stack:
        w, depth = stack.pop()
        if w is None or depth > 60 or id(w) in seen:
            continue
        seen.add(id(w))
        out.append(w)                       # <- the reference that keeps id() true
        kids = []
        if isinstance(w, Gtk.Container):
            try:
                kids = list(w.get_children())
            except Exception:                                     # noqa: BLE001
                kids = []
        for k in reversed(kids + _edges(w)):
            stack.append((k, depth + 1))
    return out


# ------------------------------------------------------------- measurements
def _markup_runs(label):
    """[(text, fg_or_None, pt_scale, weight_or_None)] for a label.

    A single Gtk.Label can hold several colours: finder.py sets a file's size in
    <span foreground="#8A857A" size="small">, which is 3.55:1 at ~10px, and no
    style-context read would ever have seen it -- the label's own colour is
    @ink. Pango exposes the resolved attributes through the layout, so the
    markup runs are measured the same way everything else here is."""
    try:
        lay = label.get_layout()
        attrs = lay.get_attributes()
    except Exception:                                             # noqa: BLE001
        attrs = None
    text = label.get_text() or ""
    if attrs is None:
        return [(text, None, 1.0, None)]
    runs = []
    try:
        it = attrs.get_iterator()
    except Exception:                                             # noqa: BLE001
        return [(text, None, 1.0, None)]
    btext = text.encode("utf-8")
    while True:
        try:
            start, end = it.range()
        except Exception:                                         # noqa: BLE001
            break
        end = min(end, len(btext))
        chunk = btext[start:end].decode("utf-8", "replace") if end > start else ""
        fg = scale = weight = None
        try:
            a = it.get(Pango.AttrType.FOREGROUND)
            if a is not None:
                c = a.as_color().color
                fg = (c.red / 257.0, c.green / 257.0, c.blue / 257.0)
        except Exception:                                         # noqa: BLE001
            fg = None
        try:
            a = it.get(Pango.AttrType.SCALE)
            if a is not None:
                scale = a.as_float().value
        except Exception:                                         # noqa: BLE001
            scale = None
        try:
            a = it.get(Pango.AttrType.WEIGHT)
            if a is not None:
                weight = int(a.as_int().value)
        except Exception:                                         # noqa: BLE001
            weight = None
        if chunk.strip():
            runs.append((chunk, fg, scale or 1.0, weight))
        try:
            if not it.next():
                break
        except Exception:                                         # noqa: BLE001
            break
    return runs or [(text, None, 1.0, None)]


def _subnode_ctx(widget, node_name):
    """A style context for a widget's CSS SUB-node (e.g. `textview text`).

    GTK3.20 gave several widgets internal nodes that app CSS legitimately
    targets -- `.nvbody text { background: #FCFBF8; }` in novel.py styles the
    text area, not the widget -- and the widget's own context knows nothing
    about them. Building the path the way the widget itself does is the only
    way to read what the glyphs are really drawn on."""
    try:
        p = widget.get_path().copy()
        p.append_type(GObject.TYPE_NONE)
        p.iter_set_object_name(-1, node_name)
        ctx = Gtk.StyleContext.new()
        ctx.set_screen(widget.get_screen() or Gdk.Screen.get_default())
        ctx.set_path(p)
        return ctx
    except Exception:                                             # noqa: BLE001
        return None


def _grade_and_record(out, app, w, text, fg, bg, pt, weight, state, source,
                      placeholder=False, disabled=False, where=None,
                      spelled_out=False):
    if not (text or "").strip():
        return
    g = grade_of(pt, weight, placeholder, disabled, text, spelled_out)
    r = ratio(fg, bg)
    out.append(Finding(app, g, r, _hex(fg), _hex(bg), _sample(text),
                       where or _where(w), state, pt, weight, source))


def measure_node(app, w, out, state_flag=Gtk.StateFlags.NORMAL,
                 state_name="rest", source="rendered"):
    """Every readable string this one widget puts on the screen."""
    ctx = w.get_style_context()
    disabled = not w.is_sensitive()

    if isinstance(w, Gtk.Label):
        base_pt, base_weight = _font(ctx, state_flag)
        fg0, alpha = _rgba(ctx, "color", state_flag)
        if alpha < 0.1:
            return
        bg = _effective_bg(w, state_flag)
        runs = _markup_runs(w)
        # Does some OTHER run of this same label say it in words?
        worded = [any(ch.isalnum() for ch in t) for t, _f, _s, _w in runs]
        for i, (text, fg, scale, weight) in enumerate(runs):
            _grade_and_record(out, app, w, text, fg if fg else fg0, bg,
                              base_pt * scale,
                              weight if weight else base_weight,
                              state_name, source,
                              placeholder=_is_listed_placeholder(app, w),
                              disabled=disabled,
                              spelled_out=any(worded[:i] + worded[i + 1:]))
        return

    if isinstance(w, Gtk.Entry):
        # The entry's own node carries both the text colour and the fill.
        pt, weight = _font(ctx, state_flag)
        bg = _effective_bg(w, state_flag)
        txt = w.get_text() or ""
        if txt.strip():
            fg, alpha = _rgba(ctx, "color", state_flag)
            if alpha >= 0.1:
                _grade_and_record(out, app, w, txt, fg, bg, pt, weight,
                                  state_name, source,
                                  placeholder=_is_listed_placeholder(app, w),
                                  disabled=disabled)
        ph = ""
        try:
            ph = w.get_placeholder_text() or ""
        except Exception:                                         # noqa: BLE001
            ph = ""
        if ph.strip():
            # GTK3 draws placeholder text in the entry's INSENSITIVE colour
            # (gtk_entry_create_layout). Reading the NORMAL colour here would
            # have scored every placeholder in the OS as if it were body text
            # and reported no defect where there might be one.
            fg, alpha = _rgba(ctx, "color", Gtk.StateFlags.INSENSITIVE)
            if alpha >= 0.1:
                _grade_and_record(out, app, w, ph, fg, bg, pt, weight,
                                  state_name, source, placeholder=True,
                                  where=_where(w) + "::placeholder")
        return

    if isinstance(w, Gtk.TextView):
        pt, weight = _font(ctx, state_flag)
        sub = _subnode_ctx(w, "text")
        fg, alpha = _rgba(sub or ctx, "color", state_flag)
        if alpha < 0.1:
            return
        bg = None
        if sub is not None:
            rgb, a = _rgba(sub, "background-color", state_flag)
            if a > 0.35:
                bg = rgb
        if bg is None:
            bg = _effective_bg(w, state_flag)
        buf = w.get_buffer()
        txt = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        # An EMPTY text view is still measured: it is where the user's own
        # words will land, and shipping a writing surface whose ink is too
        # faint is not a defect that should wait for someone to type into it.
        # A text view can BE a placeholder: cookbook swaps the buffer for the
        # prompt text and adds the class, then swaps it back on focus. The
        # evidence table is consulted here for the same reason it is consulted
        # for a label -- and it was not, at first, which held a proven
        # placeholder to the body bar and reported it as a defect.
        _grade_and_record(out, app, w, txt if txt.strip() else "Ag",
                          fg, bg, pt, weight, state_name, source,
                          placeholder=_is_listed_placeholder(app, w),
                          disabled=disabled)
        return

    if isinstance(w, Gtk.TreeView):
        measure_treeview(app, w, out, state_name, source)
        return


def _is_listed_placeholder(app, w):
    classes = w.get_style_context().list_classes()
    return any((app, c) in PLACEHOLDER_NODES for c in classes)


MAX_ROWS = 30                # enough to see per-row colouring; bounded runtime


def measure_treeview(app, tv, out, state_name, source):
    """Cell text, the way a cell renderer resolves it.

    gtk_tree_view_bin_draw() saves the view's context and adds the `cell`
    class before the renderer reads its colour, so that -- not the treeview
    node -- is what the glyphs come out of. Per-row colours set through the
    model are applied for real with cell_set_cell_data(), which is the only way
    to see a row an app has deliberately greyed."""
    model = tv.get_model()
    ctx = tv.get_style_context()
    states = [("rest", Gtk.StateFlags.NORMAL),
              ("selected", Gtk.StateFlags.SELECTED),
              ("hover", Gtk.StateFlags.PRELIGHT)]
    for label, flag in states:
        ctx.save()
        ctx.add_class(Gtk.STYLE_CLASS_CELL)
        try:
            pt, weight = _font(ctx, flag)
            fg_default, a_fg = _rgba(ctx, "color", flag)
            bg_rgb, a_bg = _rgba(ctx, "background-color", flag)
        finally:
            ctx.restore()
        if a_bg <= 0.35:
            bg_rgb = _effective_bg(tv, flag)
        if a_fg < 0.1:
            continue
        rows = []
        if model is not None:
            it = model.get_iter_first()
            n = 0
            while it is not None and n < MAX_ROWS:
                rows.append(it)
                n += 1
                it = model.iter_next(it)
        if not rows:
            rows = [None]
        for col in tv.get_columns():
            if not col.get_visible():
                continue
            for it in rows:
                if it is not None:
                    try:
                        col.cell_set_cell_data(model, it, False, False)
                    except Exception:                             # noqa: BLE001
                        pass
                for cell in col.get_cells():
                    if not isinstance(cell, Gtk.CellRendererText):
                        continue
                    try:
                        txt = cell.get_property("text")
                    except Exception:                             # noqa: BLE001
                        txt = None
                    if it is None:
                        txt = txt or "Ag"
                    if not (txt or "").strip():
                        continue
                    fg = fg_default
                    try:
                        if cell.get_property("foreground-set"):
                            c = cell.get_property("foreground-rgba")
                            if c is not None:
                                fg = (c.red * 255, c.green * 255, c.blue * 255)
                    except Exception:                             # noqa: BLE001
                        pass
                    bg = bg_rgb
                    try:
                        if cell.get_property("cell-background-set"):
                            c = cell.get_property("cell-background-rgba")
                            if c is not None and c.alpha > 0.35:
                                bg = (c.red * 255, c.green * 255, c.blue * 255)
                    except Exception:                             # noqa: BLE001
                        pass
                    _grade_and_record(
                        out, app, tv, txt, fg, bg, pt, weight, label, source,
                        where="treecell." + (col.get_title() or "?")[:16])


# ---------------------------------------------------- pass B: reached for
# Which widgets can genuinely BE hovered or chosen. This list is short on
# purpose. Papertone declares a bare `:selected { background-color: @select }`,
# so asking any widget at all "what would you look like selected?" returns
# @select -- and a gate that measured every label in the OS against the
# selection tone would report several hundred defects that no user can ever
# see. A probe must not report a verdict it did not earn (the same rule
# button_contrast_check's _effective_bg was written to obey).
SELECTABLE = (Gtk.ListBoxRow, Gtk.FlowBoxChild, Gtk.MenuItem, Gtk.IconView)


def pass_reached_for(app, win, out):
    """Measure text again with hover / selection really SET on the widget.

    Not simulated by passing a state flag to the leaf: an app writes
    `.nvparthdr:hover { background: #EAE3D2; }` on the ROW, and the caption
    inside it keeps its own colour. Only setting the state on the node that
    actually takes it, and letting GTK propagate, gives the pair of colours a
    person really sees with the pointer there."""
    nodes = list(walk(win))
    for w in nodes:
        for flag, name in ((Gtk.StateFlags.PRELIGHT, "hover"),
                           (Gtk.StateFlags.SELECTED, "selected")):
            if flag is Gtk.StateFlags.SELECTED and not isinstance(w, SELECTABLE):
                continue
            if isinstance(w, (Gtk.Label, Gtk.Entry, Gtk.TextView)):
                continue                      # a leaf is not what gets hovered
            # Only bother when the ground really moves under the state; this
            # both bounds the work and keeps the output about real changes.
            if _effective_bg(w, flag) == _effective_bg(w, Gtk.StateFlags.NORMAL):
                continue
            try:
                w.set_state_flags(flag, False)
            except Exception:                                     # noqa: BLE001
                continue
            try:
                for kid in walk(w):
                    if kid is w:
                        continue
                    measure_node(app, kid, out, Gtk.StateFlags.NORMAL, name,
                                 "rendered")
            finally:
                try:
                    w.unset_state_flags(flag)
                except Exception:                                 # noqa: BLE001
                    pass


# ------------------------------------------- pass C: declared but not reached
#
# The app's own stylesheet, read for the VOCABULARY only -- which selectors
# exist and which of them set a colour. Every colour reported from this pass is
# still one GTK computed on a real widget carrying those real classes; the CSS
# text is never parsed for a hex value. That distinction is the whole reason
# this pass is allowed to exist: a source-reading gate cannot see the cascade,
# the theme, or an app's own later override, and would be wrong in both
# directions.
BLOCK = re.compile(r'b?(?:"""|\'\'\')(.*?)(?:"""|\'\'\')', re.S)
RULE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.S)
COMMENT = re.compile(r"/\*.*?\*/", re.S)
COLOUR_DECL = re.compile(r"(?:^|;)\s*color\s*:\s*([^;]+)", re.S)
BG_DECL = re.compile(r"(?:^|;)\s*background(?:-color)?\s*:\s*([^;]+)", re.S)
SIMPLE = re.compile(r"^([A-Za-z][\w-]*)?((?:[.#][\w-]+)*)((?::[\w-]+)*)$")

PSEUDO_STATE = {
    "hover": Gtk.StateFlags.PRELIGHT,
    "selected": Gtk.StateFlags.SELECTED,
    "active": Gtk.StateFlags.ACTIVE,
    "checked": Gtk.StateFlags.CHECKED,
    "disabled": Gtk.StateFlags.INSENSITIVE,
    "insensitive": Gtk.StateFlags.INSENSITIVE,
    "focus": Gtk.StateFlags.FOCUSED,
    "backdrop": Gtk.StateFlags.BACKDROP,
}
NODE_WIDGET = {
    "label": Gtk.Label, "button": Gtk.Button, "entry": Gtk.Entry,
    "textview": Gtk.TextView, "box": Gtk.Box, "window": None,
}


def classes_used_in_code(path):
    """Every class name the module MENTIONS outside its stylesheets.

    A rule for a class nothing ever adds is dead CSS, and probing it reports a
    colour no user can see: animation.py and comics.py both carry a
    `.<app>-saved { color: @ok }` rule that no add_class() call has ever
    referred to. Counted and printed as unused rather than scored, because a
    silently dropped rule is how a real defect hides."""
    try:
        src = open(path, encoding="utf-8").read()
    except Exception:                                             # noqa: BLE001
        return None
    code = BLOCK.sub(" ", src)
    return set(re.findall(r"[\w-]+", code))


def css_selectors_with_colour(path):
    """[(selector, sets_background)] for every rule in the file that sets
    `color`. Selectors are returned verbatim; interpretation happens in the
    prober, which can refuse the ones it cannot build honestly."""
    try:
        src = open(path, encoding="utf-8").read()
    except Exception:                                             # noqa: BLE001
        return []
    out = []
    for m in BLOCK.finditer(src):
        body = COMMENT.sub(" ", m.group(1))
        if "{" not in body or "}" not in body:
            continue
        for rm in RULE.finditer(body):
            sel, decls = rm.group(1), rm.group(2)
            if not COLOUR_DECL.search(";" + decls):
                continue
            has_bg = bool(BG_DECL.search(";" + decls))
            for one in sel.split(","):
                one = " ".join(one.split())
                if one and "%" not in one:
                    out.append((one, has_bg))
    return out


# Widget types that draw NO text. A `color:` rule on one of these is styling a
# line or a glyph the toolkit paints, not something anybody reads: novel.py's
# `.nvsep { color: #D7D2C5 }` is a Gtk.Separator, and pass C dutifully reported
# it at 1.46:1 as the worst text in the app. The class-to-widget map below is
# read out of the app's own source, and it is used ONLY to EXCLUDE -- when the
# lookup fails the rule is still probed and still reported, so a gap in this
# heuristic costs a false alarm at worst and never a missed defect.
TEXTLESS = {"Separator", "DrawingArea", "Image", "ProgressBar", "LevelBar",
            "Scale", "Spinner"}

_ADDCLASS = re.compile(
    r"(\w+)(?:\.\w+\(\))*\.get_style_context\(\)\.add_class\("
    r"[\"\']([\w-]+)[\"\']\)")


def class_widget_kinds(path):
    """{css class: {Gtk widget type names it is added to}} from the source.

    Static, and deliberately so: this is not a measurement, it is the answer to
    "is there any point measuring this rule at all". The receiver of an
    add_class() call is traced back to the nearest preceding `var = Gtk.Thing(`
    in the same file."""
    try:
        src = open(path, encoding="utf-8").read()
    except Exception:                                             # noqa: BLE001
        return {}
    out = {}
    for m in _ADDCLASS.finditer(src):
        var, name = m.group(1), m.group(2)
        asg = None
        for a in re.finditer(r"\b%s\s*=\s*Gtk\.(\w+)\s*\(" % re.escape(var),
                             src[:m.start()]):
            asg = a.group(1)
        if asg:
            out.setdefault(name, set()).add(asg)
    return out


def _build_probe(selector):
    """A real widget tree matching `selector`, or None if it cannot be built.

    Returns (root_widget, leaf_widget). Refusing to build is a valid answer:
    a selector this cannot express honestly is counted and printed as
    unprobed, never silently scored as passing."""
    parts = [p for p in selector.replace(">", " ").split() if p]
    if not parts or len(parts) > 5:
        return None
    chain = []
    for i, part in enumerate(parts):
        m = SIMPLE.match(part)
        if not m:
            return None
        elem, classes, pseudos = m.group(1), m.group(2), m.group(3)
        classes = [c[1:] for c in re.findall(r"[.#][\w-]+", classes or "")]
        flags = 0
        for p in re.findall(r":([\w-]+)", pseudos or ""):
            if p in PSEUDO_STATE:
                flags |= int(PSEUDO_STATE[p])
            else:
                return None                   # :nth-child etc -- do not guess
        last = (i == len(parts) - 1)
        if elem == "window":
            if i != 0:
                return None
            chain.append(("window", classes, flags))
            continue
        if elem and elem not in NODE_WIDGET:
            return None                       # a sub-node name we cannot make
        chain.append((elem or ("label" if last else "box"), classes, flags))
    root = leaf = None
    parent = None
    for kind, classes, flags in chain:
        if kind == "window":
            w = Gtk.Window()
        elif kind == "label":
            w = Gtk.Label(label="Ag")
        elif kind == "button":
            w = Gtk.Button(label="Ag")
        elif kind == "entry":
            w = Gtk.Entry()
            w.set_text("Ag")
        elif kind == "textview":
            w = Gtk.TextView()
            w.get_buffer().set_text("Ag")
        else:
            w = Gtk.Box()
        for c in classes:
            w.get_style_context().add_class(c)
        if flags:
            w.set_state_flags(Gtk.StateFlags(flags), False)
        if parent is not None:
            try:
                parent.add(w)
            except Exception:                                     # noqa: BLE001
                return None
        else:
            root = w
        parent, leaf = w, w
    synthetic = not isinstance(root, Gtk.Window)
    if synthetic:
        # A widget with no window ancestor has no style path to resolve
        # against, so one is provided -- and remembered, because its own
        # papertone fill is scaffolding, not the app's ground. Missing that
        # distinction made every light-on-slab rule look like paper-on-paper:
        # g2048's score tile came out at 1.00:1, the worst verdict this tool
        # has, for text that is white on black and perfectly legible.
        holder = Gtk.Window()
        holder.add(root)
        root = holder
    root.show_all()
    return root, leaf, synthetic


# The luminance of @hair (#C9C4B6). A text colour LIGHTER than the palette's
# mid neutral is, by construction, meant for a dark slab -- g2048's score tile
# sets `.score-val { color: #FCFBF8 }` with the ink ground on the surrounding
# `.scorebox`, and the first run of this pass duly reported it at 1.00:1 as the
# worst defect in the OS. It is white on black and perfectly legible. Those
# rules are counted as UNPROBED, not scored: this pass cannot see a ground it
# was not given, and a manufactured defect is worse than a missed one, because
# somebody then "fixes" a correct design to satisfy it.
LIGHT_INK = _lum((201, 196, 182))


def _declared_ground(leaf, rule_has_bg, stop_at=None):
    """The ground a declared-but-unreached rule is graded against.

    Three cases, in order of how much is really known:

      1. The rule paints its own background -- nothing is assumed.
      2. The selector is a DESCENDANT selector, so the probe built the
         ancestors too and one of them carries a fill. Also nothing assumed:
         `.card .cap` is measured on `.card`'s real ground.
      3. A bare leaf selector with no fill anywhere in its chain. Then the
         ground is ASSUMED to be @paper and the finding says so. Paper is the
         lightest surface in tools/design_tokens.py, so for the dark inks in
         case 3 (see LIGHT_INK above, which is what keeps light-on-slab rules
         out of this pass entirely) it is the ground that flatters the text
         MOST of all the OS's papertone grounds. An assumption that can only
         under-report is the only kind a gate may make.

    Returns (rgb, assumed)."""
    if rule_has_bg:
        own, alpha = _rgba(leaf.get_style_context(), "background-color")
        if alpha > 0.35:
            return own, False
    w = leaf
    for _ in range(8):
        if w is stop_at:
            break
        rgb, alpha = _rgba(w.get_style_context(), "background-color")
        if alpha > 0.35:
            return rgb, False
        w = w.get_parent()
        if w is None:
            break
    return (252, 251, 248), True


def pass_declared(app, out, reached_classes):
    path = os.path.join(DE, app + ".py")
    kinds = class_widget_kinds(path)
    used = classes_used_in_code(path)
    unprobed = unused = 0
    seen = set()
    for selector, has_bg in css_selectors_with_colour(path):
        leaf_classes = set(re.findall(r"[.#]([\w-]+)",
                                      selector.split()[-1] if selector else ""))
        if leaf_classes and leaf_classes <= reached_classes:
            continue                          # pass A already measured it
        if leaf_classes and all(kinds.get(c) and kinds[c] <= TEXTLESS
                                for c in leaf_classes):
            continue                          # a line, not a word: see TEXTLESS
        if used is not None and leaf_classes and not (leaf_classes & used):
            unused += 1                       # dead CSS: see classes_used_in_code
            continue
        if selector in seen:
            continue
        seen.add(selector)
        built = _build_probe(selector)
        if built is None:
            unprobed += 1
            continue
        root, leaf, synthetic = built
        try:
            ctx = leaf.get_style_context()
            fg, alpha = _rgba(ctx, "color")
            if alpha < 0.1:
                continue
            pt, weight = _font(ctx)
            bg, assumed = _declared_ground(leaf, has_bg,
                                           stop_at=root if synthetic else None)
            if assumed and _lum(fg) >= LIGHT_INK:
                unprobed += 1        # light ink, ground unknown: see LIGHT_INK
                continue
            probe_classes = set(re.findall(r"[.#]([\w-]+)", selector))
            disabled = (":disabled" in selector or ":insensitive" in selector
                        or any((app, c) in DISABLED_NODES
                               for c in probe_classes))
            ph = any((app, c) in PLACEHOLDER_NODES for c in leaf_classes)
            _grade_and_record(out, app, leaf, "Ag", fg, bg, pt, weight,
                              "rest", "declared", placeholder=ph,
                              disabled=disabled,
                              where="css " + selector[:34]
                                    + (" @paper?" if assumed else ""))
        finally:
            try:
                root.destroy()
            except Exception:                                     # noqa: BLE001
                pass
    return unprobed, unused


# ------------------------------------------------------------------ one app
def construct(mod_name):
    """The app's main window, settled.

    The 300ms wait is not padding: app CSS classes applied during __init__ ride
    a real 90ms Papertone transition driven by the GDK frame clock's own timer,
    so a colour read at zero main-loop iterations can land anywhere along the
    curve. button_contrast_check.check_app carries the full argument and the
    measurements behind it; this is the same discipline, and must stay the
    same."""
    import importlib
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    m = importlib.import_module(mod_name)
    cls = None
    for name in dir(m):
        obj = getattr(m, name)
        if isinstance(obj, type) and issubclass(obj, Gtk.Window) \
                and obj.__module__ == m.__name__:
            cls = obj
            break
    if cls is None:
        return None
    win = cls()
    GLib.timeout_add(300, Gtk.main_quit)
    Gtk.main()
    return win


def check_app(app):
    """[Finding], unprobed_selector_count -- everything readable in one app."""
    out = []
    win = construct(app)
    reached = set()
    if win is not None:
        for w in walk(win):
            reached.update(w.get_style_context().list_classes())
            measure_node(app, w, out)
        pass_reached_for(app, win, out)
    unprobed, unused = pass_declared(app, out, reached)
    if win is not None:
        try:
            win.destroy()
        except Exception:                                         # noqa: BLE001
            pass
    return out, unprobed, unused


# ------------------------------------------------------------------ reporting
def failures(findings):
    bad = {}
    for f in findings:
        if f.ratio >= BARS[f.grade] - 1e-9:
            continue
        cur = bad.get(f.key)
        if cur is None:
            bad[f.key] = [f, 1]
        else:
            cur[1] += 1
            if f.ratio < cur[0].ratio:
                cur[0] = f
    return [(v[0], v[1]) for v in bad.values()]


def print_findings(app, rows, stream=sys.stdout):
    for f, n in sorted(rows, key=lambda t: t[0].ratio):
        tag = "GONE" if f.ratio < BAR_INVISIBLE else "LOW"
        stream.write(
            "%-4s %-13s %5.2f:1 (bar %.1f) %-11s %-8s fg=%s bg=%s x%-3d %-36s %r\n"
            % (tag, app, f.ratio, BARS[f.grade], f.grade, f.state, f.fg, f.bg,
               n, f.where[:36], f.text))


# ------------------------------------------------------------------- driver
def run_one(app):
    """In-process, one app. Printed in a form the parent can parse back."""
    uishot.load_theme()
    findings, unprobed, unused = check_app(app)
    rows = failures(findings)
    print_findings(app, rows)
    print("TC-STATS %s measured=%d failing=%d nodes=%d unprobed=%d unused=%d"
          % (app, len(findings), len(rows),
             sum(n for _f, n in rows), unprobed, unused))
    return 0


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    flags = set(a for a in argv if a.startswith("--"))
    if "--selfcheck" in flags:
        return selfcheck()
    if "--one" in flags:
        return run_one(args[0])
    apps = args or app_list()
    inproc = "--inproc" in flags
    total_fail = total_nodes = total_measured = total_unprobed = 0
    total_unused = 0
    errors = 0
    probed = 0
    if inproc:
        uishot.load_theme()
    for app in apps:
        if inproc:
            # Every app's CSS is installed on the SCREEN and never removed, so
            # in one process app N is styled by apps 1..N-1 as well. It is kept
            # only as an escape hatch for debugging a single app quickly.
            try:
                findings, unprobed, unused = check_app(app)
            except Exception as exc:                              # noqa: BLE001
                print("ERR  %-13s %s" % (app, str(exc)[:70]))
                errors += 1
                continue
            rows = failures(findings)
            print_findings(app, rows)
            probed += 1
            total_measured += len(findings)
            total_fail += len(rows)
            total_nodes += sum(n for _f, n in rows)
            total_unprobed += unprobed
            total_unused += unused
            continue
        env = dict(os.environ)
        try:
            p = subprocess.run(
                [sys.executable, os.path.abspath(__file__), "--one", app],
                capture_output=True, text=True, env=env, timeout=300)
        except subprocess.TimeoutExpired:
            # An app that will not settle is not a pass. Counted as a probe
            # error, which is already part of the FAILED verdict, rather than
            # allowed to take the whole run down with a traceback and lose the
            # 42 results already gathered.
            print("ERR  %-13s timed out after 300s" % app)
            errors += 1
            continue
        stats = None
        for line in p.stdout.splitlines():
            if line.startswith("TC-STATS "):
                stats = line.split()
            else:
                print(line)
        if p.returncode != 0 or stats is None:
            err = (p.stderr or "").strip().splitlines()
            print("ERR  %-13s %s" % (app, err[-1][:70] if err else "no verdict"))
            errors += 1
            continue
        probed += 1
        vals = dict(kv.split("=") for kv in stats[2:])
        total_measured += int(vals["measured"])
        total_fail += int(vals["failing"])
        total_nodes += int(vals["nodes"])
        total_unprobed += int(vals["unprobed"])
        total_unused += int(vals["unused"])

    # "N checks" in the runner's COUNTED grammar, and ONE CHECK IS ONE APP on
    # purpose. run_all_gates uses this number to tell a gate that crashed on
    # its first subject from a gate that ran everything and found a defect --
    # so it has to be a count that only goes down when coverage really does.
    # The node total moves with what happens to be in NB_HOME on the day; the
    # number of apps inspected does not.
    print("\n%d checks: %d app(s) inspected, %d text node(s) measured, "
          "%d probe error(s)"
          % (probed, probed, total_measured, errors))
    print("%d distinct failing rule(s), covering %d node(s)"
          % (total_fail, total_nodes))
    # Coverage, stated. A selector this tool could not build a probe for is not
    # a pass; printing the number is the difference between a gate that knows
    # its blind spot and one that hides in it.
    print("%d css colour selector(s) could not be probed (reported, not scored)"
          % total_unprobed)
    print("%d css colour rule(s) name a class no code ever adds (dead, skipped)"
          % total_unused)
    failed = bool(total_fail or errors or probed != len(apps))
    print("RESULT: %s" % ("FAILED (%d rule(s) under bar, %d probe error(s))"
                          % (total_fail, errors) if failed else "PASS"))
    return 1 if failed else 0


# ---------------------------------------------------------------- selfcheck
#
# The gate has to be shown going RED on a defect it is supposed to catch, on
# the real tree, and then shown going green again. A green gate nobody has ever
# seen fail is a decoration.
SELFCHECK_APP = "g2048"


def selfcheck():
    import shutil
    import tempfile
    src = os.path.join(DE, SELFCHECK_APP + ".py")
    if not os.path.exists(src):
        print("FAIL: selfcheck subject %s missing" % src)
        return 1
    body = open(src, encoding="utf-8").read()
    # A REAL rule in the file, not one we add: find a colour declaration in the
    # app's own stylesheet and darken-to-invisible the ink it names. Sabotaging
    # a rule the app really has is the only version of this that proves the
    # path from "someone edits the CSS" to "the gate goes red".
    m = re.search(r"(color\s*:\s*)#1A1916", body) or \
        re.search(r"(color\s*:\s*)#2A2620", body)
    if m is None:
        print("FAIL: no ink colour rule to sabotage in %s" % SELFCHECK_APP)
        return 1
    line = body.count("\n", 0, m.start()) + 1
    print("sabotage target: %s.py line %d  %r -> `%s#E8E3D8`"
          % (SELFCHECK_APP, line, body[m.start():m.end()], m.group(1)))
    tmp = tempfile.mkdtemp(prefix="tc-selfcheck-")
    try:
        for entry in os.listdir(DE):
            os.symlink(os.path.join(DE, entry), os.path.join(tmp, entry))
        os.unlink(os.path.join(tmp, SELFCHECK_APP + ".py"))
        env = dict(os.environ)
        env["NB_TC_DE"] = tmp

        def run(label):
            p = subprocess.run(
                [sys.executable, os.path.abspath(__file__), SELFCHECK_APP],
                capture_output=True, text=True, env=env, timeout=300)
            print("--- %s ---" % label)
            print(p.stdout.rstrip())
            return p.returncode, p.stdout

        # 1. the untouched file, through the same path: must be green, or the
        #    red below proves nothing.
        shutil.copyfile(src, os.path.join(tmp, SELFCHECK_APP + ".py"))
        rc_before, out_before = run("BEFORE: %s untouched" % SELFCHECK_APP)
        if rc_before != 0 or "RESULT: PASS" not in out_before:
            print("FAIL: %s is not green to begin with, so a red proves "
                  "nothing about the sabotage" % SELFCHECK_APP)
            return 1

        # 2. sabotage one real declaration: ink -> a near-ground papertone.
        broken = body[:m.start()] + m.group(1) + "#E8E3D8" + body[m.end():]
        open(os.path.join(tmp, SELFCHECK_APP + ".py"), "w",
             encoding="utf-8").write(broken)
        rc_after, out_after = run("AFTER: one real `%s#1A1916` rule set to "
                                  "#E8E3D8" % m.group(1))
        if rc_after == 0 or "RESULT: FAILED" not in out_after:
            print("FAIL: the gate stayed green on unreadable text")
            return 1

        # 3. restore, and prove the red went away with the sabotage.
        shutil.copyfile(src, os.path.join(tmp, SELFCHECK_APP + ".py"))
        rc_restored, out_restored = run("RESTORED")
        if rc_restored != 0 or "RESULT: PASS" not in out_restored:
            print("FAIL: still red after restore -- the red was not the "
                  "sabotage")
            return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nPASS: green -> red on a real sabotaged rule -> green on restore")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
