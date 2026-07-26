#!/usr/bin/env python3
"""nbdiacritics — press-and-hold accent picker, the way a laptop should do it.

This image ships a US keyboard layout by default and no compose key, so the
accented letters half the desktop needs — the Spanish, French, Esperanto and
Serbo-Croatian the Language app teaches, never mind a name like "Muñoz" in
Contacts — were simply untypeable. Hold a letter down and a small palette of its
accented forms opens over the app:

    hold "e"  ->   1 é   2 è   3 ê   4 ë   5 ē   6 ę   7 ė   8 ě   9 €

Press 1-9 to take one, arrow keys to walk the row and Return to take the
highlighted one, Esc (or any other key) to dismiss and keep the plain letter you
already typed. Clicking a tile picks it too.

It is installed by the nbapp base window, so every app's Gtk.Entry and
Gtk.TextView gets it with no per-app code — the same way nbpinyin installs the
Chinese IME.

Two details worth knowing:

* The first press types the plain letter immediately, exactly as it always did;
  picking an accent REPLACES that letter. So a hold that you abandon costs you
  nothing, and the picker never has to guess what you meant before you do.
* Holding a key that carries accents no longer auto-repeats it (holding "j" will
  not fill a line with jjjj). Keys with no accents — Backspace, arrows, space,
  digits — repeat exactly as before. That is the same trade every desktop with
  this feature makes, and it is why the table below is deliberately small.

Hold detection has to work on plain X11 with no XKB detectable-autorepeat: X
sends a release/press PAIR for every repeat, so a release is only believed after
REPEAT_GRACE_MS has passed with no matching press (an autorepeat press arrives
within a millisecond, a human re-press never does).
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

# How long a key must be held before the palette opens. Long enough that normal
# typing never trips it, short enough to feel like a gesture rather than a wait.
HOLD_MS = 450
# A release is only real if no press of the same key follows within this window.
# X autorepeat's press lands in well under a millisecond; 20ms is far below the
# ~80ms a human needs to re-press a key, so the two can't be confused.
REPEAT_GRACE_MS = 20
MAX_ITEMS = 9                   # the palette is picked with the 1-9 keys

# Accents per base letter, most-used first — the 1 key is the easiest to hit, so
# the order is "what would a user of this OS most likely want", not codepoint
# order. Coverage is aimed at the courses the Language app actually teaches
# (Spanish, French, Esperanto, Serbo-Croatian) plus the rest of Western/Central
# Europe, with a few otherwise-untypeable symbols riding on spare slots.
_LOWER = {
    "a": "áàâäãåāą",
    "c": "çćčĉċ©",
    "d": "đďð",
    "e": "éèêëēęėě€",
    "g": "ĝğģġ",
    "h": "ĥ",
    "i": "íìîïīį",
    "j": "ĵ",
    "k": "ķ",
    "l": "łľĺļ",
    "m": "µ",
    "n": "ñńňņ",
    "o": "óòôöõøōœ",
    "p": "¶§",
    "r": "řŕ®",
    "s": "šśŝşșß",
    "t": "ťțţþ",
    "u": "úùûüŭūů",
    "w": "ŵ",
    "x": "×",
    "y": "ýÿŷ",
    "z": "žźż",
}

# Punctuation. ¿ and ¡ matter as much as any accent here: Spanish is a shipped
# course and neither is reachable on a US layout.
#
# Every key listed here LOSES its autorepeat, so the test for including one is
# "would anyone hold this key down to get a row of them?". That rules out = + *
# and the digits — nobody misses ¾ badly enough to break ===== and *****, which
# people really do type in notes and code. The em/en dash keeps its place on -
# despite the same risk: this is a writing OS (Writer, Novel, Screenplay,
# Journal, Academic) and a proper dash earns its keep in a way ¾ does not.
_PUNCT = {
    "?": "¿",
    "!": "¡",
    "-": "–—·",
    ".": "…",
    ",": "„",
    "'": "’‘‚′",
    '"': "“”„«»″",
    "/": "÷",
    "<": "‹«",
    ">": "›»",
    "$": "€£¥¢",
    "0": "°",
}


def _build_table():
    """base character -> tuple of variants, lowercase + derived uppercase."""
    tbl = {}
    for base, vs in _LOWER.items():
        tbl[base] = tuple(vs)[:MAX_ITEMS]
        # Uppercase row derived from the lowercase one so the two can never
        # drift apart. str.upper() is per-character here, and anything that does
        # not survive as a single character (ß -> SS) is simply dropped.
        up = [v.upper() for v in vs]
        up = [v for v in up if len(v) == 1]
        if up:
            tbl[base.upper()] = tuple(dict.fromkeys(up))[:MAX_ITEMS]
    for base, vs in _PUNCT.items():
        tbl[base] = tuple(vs)[:MAX_ITEMS]
    return tbl


TABLE = _build_table()

CSS = b"""
.nbdia { background: #F8F7F2; border: 1px solid #C9C4B6; padding: 4px;
         box-shadow: 3px 3px 0 rgba(26,25,22,0.15); }
.nbdia-key { background: transparent; border: 1px solid #F8F7F2;
             border-radius: 2px; padding: 3px 8px 4px 8px; box-shadow: none;
             color: #1A1916; }
.nbdia-key:hover { background: #EAE3D2; }
/* Selection is the warm beige the dropdowns use - never black, and never the
   signage red, which this design language keeps for today/alerts only. */
.nbdia-key.on { background: #EAE3D2; border: 1px solid #C9C4B6; }
.nbdia-ch { font-family: "Nimbus Sans","Helvetica",sans-serif; font-size: 21px;
            color: #1A1916; }
.nbdia-ix { font-family: "Nimbus Sans","Helvetica",sans-serif; font-size: 10px;
            color: #9A9484; }
"""
_CSS_PROV = None


def _css():
    global _CSS_PROV
    if _CSS_PROV is None:
        _CSS_PROV = Gtk.CssProvider()
        _CSS_PROV.load_from_data(CSS)
    return _CSS_PROV


def _style(widget, cls):
    ctx = widget.get_style_context()
    ctx.add_class(cls)
    ctx.add_provider(_css(), Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


class DiacriticsPicker:
    """One instance per app window. Like nbpinyin it listens at the toplevel,
    which is delivered keys before the focused text widget, so it can swallow
    autorepeat and drive the palette without any cooperation from the app."""

    def __init__(self, window):
        self.win = window
        self._held = None        # (keyval, base_char) of the key being held
        self._hold_src = 0       # timer: hold elapsed -> open the palette
        self._rel_src = 0        # timer: deferred "the key really came up"
        self._open = False
        self._items = ()
        self._sel = 0
        self._base = ""
        self._target = None      # text widget the palette will insert into
        self._layer = None       # overlay layer (nbapp) ...
        self._popup = None       # ... or a popup window when there's no overlay
        self._dead = False
        window.connect("key-press-event", self._on_press)
        window.connect("key-release-event", self._on_release)
        window.connect("destroy", self._on_destroy)

    # ---- lifecycle --------------------------------------------------------
    def _on_destroy(self, *_):
        # Timers outlive the widget they were armed for; drop them or the
        # callback fires against a destroyed window during teardown.
        self._dead = True
        self._cancel_hold()
        self._cancel_release()
        return False

    def _cancel_hold(self):
        if self._hold_src:
            GLib.source_remove(self._hold_src)
            self._hold_src = 0

    def _cancel_release(self):
        if self._rel_src:
            GLib.source_remove(self._rel_src)
            self._rel_src = 0

    def _abandon(self):
        """Stop tracking a hold that will not become a palette."""
        self._cancel_hold()
        self._cancel_release()
        self._held = None

    # ---- eligibility ------------------------------------------------------
    def _focus_text(self):
        """The focused widget, if it is one we can insert a character into."""
        w = self.win.get_focus()
        if isinstance(w, Gtk.TextView):
            return w if w.get_editable() else None
        if isinstance(w, Gtk.Editable):
            # A spin button's content is a number; offering it ¹½¼ is nonsense.
            if isinstance(w, Gtk.SpinButton):
                return None
            try:
                # Never pop a palette over a password field: it would echo the
                # character being typed in plain sight.
                if hasattr(w, "get_visibility") and not w.get_visibility():
                    return None
                if not w.get_editable():
                    return None
            except Exception:
                return None
            return w
        return None

    def _pinyin_busy(self):
        """True while the Chinese IME owns the keyboard, so the two input
        methods can never both act on one keystroke."""
        ime = getattr(self.win, "_pinyin", None)
        return bool(ime is not None and (getattr(ime, "active", False)
                                         or getattr(ime, "buffer", "")))

    def _eligible(self, ev, ch):
        if len(ch) != 1 or ch not in TABLE:
            return False
        # Ctrl/Alt/Super mean an accelerator, not typing. Shift is fine — it is
        # how the uppercase rows are reached.
        if ev.state & (Gdk.ModifierType.CONTROL_MASK
                       | Gdk.ModifierType.MOD1_MASK
                       | Gdk.ModifierType.SUPER_MASK):
            return False
        if self._pinyin_busy():
            return False
        return self._focus_text() is not None

    # ---- key handling -----------------------------------------------------
    def _on_press(self, _w, ev):
        if self._dead:
            return False
        if self._open:
            return self._palette_key(ev)
        kv = ev.keyval
        if self._held is not None and kv == self._held[0]:
            # An autorepeat press of the key being held. Swallow it so the
            # letter is not typed over and over, and — if X's repeat delay beat
            # our HOLD_MS timer — treat the repeat itself as the hold.
            self._cancel_release()
            self._show()
            return True
        # Any other key means the user is typing, not holding.
        self._abandon()
        ch = ev.string or ""
        if not self._eligible(ev, ch):
            return False
        self._held = (kv, ch)
        self._hold_src = GLib.timeout_add(HOLD_MS, self._on_hold_elapsed)
        return False        # let the plain letter type normally

    def _on_release(self, _w, ev):
        if self._dead or self._held is None or ev.keyval != self._held[0]:
            return False
        # Do not believe it yet: X sends release+press for every autorepeat.
        self._cancel_release()
        self._rel_src = GLib.timeout_add(REPEAT_GRACE_MS, self._real_release)
        return False

    def _real_release(self):
        self._rel_src = 0
        if self._open:
            # The palette stays up after the key comes up — you let go, then
            # choose. Just stop tracking the key itself.
            self._held = None
        else:
            self._abandon()
        return False

    def _on_hold_elapsed(self):
        self._hold_src = 0
        self._show()
        return False

    def _palette_key(self, ev):
        kv = ev.keyval
        if kv == Gdk.KEY_Escape:
            self._close()
            return True
        if Gdk.KEY_1 <= kv <= Gdk.KEY_9:
            self._commit(kv - Gdk.KEY_1)
            return True
        if kv in (Gdk.KEY_Left, Gdk.KEY_Up):
            self._move(-1)
            return True
        if kv in (Gdk.KEY_Right, Gdk.KEY_Down, Gdk.KEY_Tab):
            self._move(1)
            return True
        if kv in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            self._commit(self._sel)
            return True
        # Anything else: dismiss and let the keystroke through, so typing
        # straight past an unwanted palette just works.
        self._close()
        return False

    def _move(self, step):
        if not self._items:
            return
        self._sel = (self._sel + step) % len(self._items)
        self._sync_selection()

    # ---- replace the typed letter with the chosen accent ------------------
    def _commit(self, idx):
        if not (0 <= idx < len(self._items)):
            self._close()
            return
        variant = self._items[idx]
        tgt, base = self._target, self._base
        self._close()
        if tgt is None:
            return
        try:
            if isinstance(tgt, Gtk.TextView):
                buf = tgt.get_buffer()
                end = buf.get_iter_at_mark(buf.get_insert())
                start = end.copy()
                # Only delete what we are sure we put there. If the app moved
                # the caret or transformed the text under us, insert without
                # deleting — a stray letter is recoverable, eaten text is not.
                if start.backward_char() and buf.get_text(start, end, False) == base:
                    buf.delete(start, end)
                buf.insert_at_cursor(variant)
            else:
                pos = tgt.get_position()
                if pos > 0 and tgt.get_chars(pos - 1, pos) == base:
                    tgt.delete_text(pos - 1, pos)
                    pos -= 1
                    tgt.set_position(pos)
                # PyGObject overrides Gtk.Editable.insert_text to (text,
                # position) — the C function's length argument is supplied by
                # the override. Passing it here raises TypeError, which an
                # except: pass would turn into "the letter just vanishes".
                tgt.set_position(tgt.insert_text(variant, pos))
        except Exception:
            pass

    # ---- the palette ------------------------------------------------------
    def _show(self):
        if self._open or self._dead:
            return
        self._cancel_hold()
        if self._held is None:
            return
        base = self._held[1]
        items = TABLE.get(base) or ()
        tgt = self._focus_text()
        if not items or tgt is None:
            self._abandon()
            return
        self._items = items
        self._sel = 0
        self._base = base
        self._target = tgt
        self._buttons = []

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        _style(row, "nbdia")
        for i, ch in enumerate(items):
            b = Gtk.Button()
            b.set_relief(Gtk.ReliefStyle.NONE)
            _style(b, "nbdia-key")
            cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            lab = Gtk.Label(label=ch)
            _style(lab, "nbdia-ch")
            ix = Gtk.Label(label=str(i + 1))
            _style(ix, "nbdia-ix")
            cell.pack_start(lab, False, False, 0)
            cell.pack_start(ix, False, False, 0)
            b.add(cell)
            b.connect("clicked", lambda _b, n=i: self._commit(n))
            row.pack_start(b, False, False, 0)
            self._buttons.append(b)
        self._open = True
        self._sync_selection()
        self._mount(row)

    def _sync_selection(self):
        for i, b in enumerate(getattr(self, "_buttons", [])):
            ctx = b.get_style_context()
            if i == self._sel:
                ctx.add_class("on")
            else:
                ctx.remove_class("on")

    def _caret_xy(self, tgt, overlay):
        """Where to put the palette: just under the caret, in overlay coords."""
        try:
            if isinstance(tgt, Gtk.TextView):
                buf = tgt.get_buffer()
                it = buf.get_iter_at_mark(buf.get_insert())
                r = tgt.get_iter_location(it)
                wx, wy = tgt.buffer_to_window_coords(
                    Gtk.TextWindowType.WIDGET, r.x, r.y)
                xy = tgt.translate_coordinates(overlay, wx, wy + r.height + 2)
                if xy:
                    return xy
            else:
                a = tgt.get_allocation()
                xy = tgt.translate_coordinates(overlay, 0, a.height + 2)
                if xy:
                    return xy
        except Exception:
            pass
        return (14, 60)

    def _mount(self, row):
        """Draw inside the app window when we can. A Gtk.Overlay child needs no
        second toplevel, which on this no-compositor stack is the difference
        between a palette that reliably paints on top and one that does not —
        the same reason nbapp draws its dropdown menus this way."""
        overlay = getattr(self.win, "_overlay", None)
        if overlay is None:
            self._mount_popup(row)
            return
        try:
            layer = Gtk.Fixed()
            scrim = Gtk.EventBox()
            scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            alloc = self.win.get_allocation()
            W = alloc.width if alloc.width > 1 else 1920
            H = alloc.height if alloc.height > 1 else 1080
            scrim.set_size_request(W, H)
            scrim.connect("button-press-event", lambda *_a: (self._close(), True)[1])
            layer.put(scrim, 0, 0)
            holder = Gtk.EventBox()     # own GdkWindow so it blits (see nbapp)
            holder.add(row)
            layer.put(holder, 0, 0)
            overlay.add_overlay(layer)
            layer.show_all()
            x, y = self._caret_xy(self._target, overlay)
            _min, nat = holder.get_preferred_size()
            w = nat.width if nat.width > 1 else 0
            h = nat.height if nat.height > 1 else 0
            # Keep it on screen: a caret near the right or bottom edge would
            # otherwise push the palette off the panel.
            x = min(max(int(x), 0), max(W - w, 0))
            if y + h > H:
                y = max(int(y) - h - 24, 0)     # flip above the caret line
            layer.move(holder, x, max(int(y), 0))
            for win in (layer.get_window(), holder.get_window()):
                if win is not None:
                    win.raise_()
            self._layer = layer
        except Exception:
            self._layer = None
            self._mount_popup(row)

    def _mount_popup(self, row):
        """Fallback for windows without nbapp's overlay (e.g. a bare dialog)."""
        try:
            pop = Gtk.Window(type=Gtk.WindowType.POPUP)
            pop.set_type_hint(Gdk.WindowTypeHint.TOOLTIP)
            pop.set_transient_for(self.win)
            pop.add(row)
            gdkwin = self._target.get_window() or self.win.get_window()
            if gdkwin is not None:
                res = gdkwin.get_origin()       # (x,y) or (ok,x,y) per binding
                a = self._target.get_allocation()
                pop.move(res[-2] + 6, res[-1] + a.height + 2)
            pop.show_all()
            self._popup = pop
        except Exception:
            self._popup = None
            self._open = False

    def _close(self):
        self._open = False
        self._items = ()
        self._buttons = []
        self._target = None
        self._abandon()
        if self._layer is not None:
            try:
                overlay = getattr(self.win, "_overlay", None)
                if overlay is not None:
                    overlay.remove(self._layer)
            except Exception:
                pass
            self._layer = None
        if self._popup is not None:
            try:
                self._popup.destroy()
            except Exception:
                pass
            self._popup = None
