#!/usr/bin/env python3
"""nbpinyin — a compact Pinyin input method for Chinese.

This image ships no ibus/fcitx, so Chinese is typed with this instead: press
Ctrl+Space in any text field to toggle it on, type pinyin, and a candidate
popup shows Hanzi (frequency-ordered, from a Rime-derived dictionary). Pick a
candidate with 1–9 or Space, page with -/= (or ↑/↓), Backspace edits the pinyin,
Esc cancels. It is installed by the nbapp base window, so it works in every
app's Gtk.Entry / Gtk.TextView with no per-app code.
"""
import os
import json
import lzma
import bisect

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, Pango  # noqa: E402

DICT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "pinyin.dict.xz")
PAGE = 9


def _pinyin_letter(ch):
    """One ASCII letter — the whole alphabet pinyin is written in.

    `str.isalpha()`, which this used, is true of every Unicode letter, so é, ü,
    Cyrillic д and even 好 itself were appended to the composition. The buffer
    then matched nothing, the popup emptied, and the keystroke was SWALLOWED —
    so on any layout that can produce an accented letter the composition died
    with no visible cause. A character that cannot be part of a pinyin syllable
    is handled the way punctuation already is: commit what is there and let it
    through.
    """
    return len(ch) == 1 and ("a" <= ch <= "z" or "A" <= ch <= "Z")


class PinyinIME:
    """One instance per app window; intercepts keys at the toplevel so it runs
    before the focused text widget."""

    _dict = None            # class-shared, lazy-loaded once per process
    _keys = None

    def __init__(self, window):
        self.win = window
        self.active = False
        self.buffer = ""
        self.cands = []
        self.page = 0
        self._composition_target = None
        self.popup = None
        self._pop_label = None
        # key-press on the toplevel is delivered before the focus widget, so a
        # handler that returns True keeps the raw pinyin out of the entry.
        window.connect("key-press-event", self._on_key)

    # ---- dictionary -------------------------------------------------------
    @classmethod
    def _load(cls):
        if cls._dict is not None:
            return
        try:
            with open(DICT_PATH, "rb") as f:
                cls._dict = json.loads(lzma.decompress(f.read()).decode("utf-8"))
        except (OSError, ValueError):
            cls._dict = {}
        cls._keys = sorted(cls._dict.keys())

    def _lookup(self, p):
        self._load()
        d = self._dict
        res = list(d.get(p, []))
        seen = set(res)
        ks = self._keys
        i = bisect.bisect_left(ks, p)          # prefix completions
        n = 0
        while i < len(ks) and ks[i].startswith(p) and n < 30:
            if ks[i] != p:
                for w in d[ks[i]][:3]:
                    if w not in seen:
                        seen.add(w)
                        res.append(w)
                n += 1
            i += 1
        return res[:50]

    # ---- key handling -----------------------------------------------------
    def _focus_text(self):
        w = self.win.get_focus()
        if isinstance(w, Gtk.TextView):
            return w if w.get_editable() else None
        if isinstance(w, Gtk.Editable):
            if isinstance(w, Gtk.SpinButton):
                return None
            try:
                # Candidate text must never reveal a masked composition, and
                # programmatic insertion must respect a read-only field.
                if hasattr(w, "get_visibility") and not w.get_visibility():
                    return None
                if not w.get_editable():
                    return None
            except Exception:
                return None
            return w
        return None

    def _on_key(self, win, ev):
        ctrl = ev.state & Gdk.ModifierType.CONTROL_MASK
        alt = ev.state & Gdk.ModifierType.MOD1_MASK
        if ctrl and ev.keyval == Gdk.KEY_space:      # toggle
            self.active = not self.active
            self._reset()
            return True
        if not self.active:
            return False
        tgt = self._focus_text()
        if tgt is None:
            return False
        if self.buffer and tgt is not self._composition_target:
            # Composition belongs to the field where its first letter was
            # swallowed. Never redirect its eventual commit after Tab/click.
            self._reset()
            return False
        kv = ev.keyval
        ch = ev.string or ""

        if self.buffer:
            super_mod = ev.state & Gdk.ModifierType.SUPER_MASK
            if ctrl or alt or super_mod:
                # Accelerators belong to the app. Do not turn Ctrl+C/Ctrl+Z or
                # an Alt/Super shortcut into an unexpected Hanzi insertion.
                self._reset()
                return False
            if kv == Gdk.KEY_Escape:
                self._reset()
                return True
            if kv == Gdk.KEY_BackSpace:
                self.buffer = self.buffer[:-1]
                if self.buffer:
                    self._update()
                else:
                    self._reset()
                return True
            if kv in (Gdk.KEY_space, Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                if self.cands:
                    self._commit(tgt, self.cands[self.page * PAGE])
                else:
                    self._commit(tgt, self.buffer)
                return True
            if Gdk.KEY_1 <= kv <= Gdk.KEY_9:
                idx = self.page * PAGE + (kv - Gdk.KEY_1)
                if idx < len(self.cands):
                    self._commit(tgt, self.cands[idx])
                    return True
                return True
            if kv in (Gdk.KEY_minus, Gdk.KEY_Page_Up, Gdk.KEY_Up):
                if self.page > 0:
                    self.page -= 1
                    self._show()
                return True
            if kv in (Gdk.KEY_equal, Gdk.KEY_plus, Gdk.KEY_Page_Down, Gdk.KEY_Down):
                if (self.page + 1) * PAGE < len(self.cands):
                    self.page += 1
                    self._show()
                return True
            if _pinyin_letter(ch) and not ctrl and not alt:
                self.buffer += ch.lower()
                self._update()
                return True
            # any other key (punctuation, etc.): commit the top candidate and
            # let the key through so "nihao," -> "你好,"
            if self.cands:
                self._commit(tgt, self.cands[self.page * PAGE])
            else:
                # Unknown/typo compositions are still authored text.  Space
                # and Return already preserve the raw buffer; punctuation
                # must do the same before GTK inserts the punctuation itself.
                self._commit(tgt, self.buffer)
            return False
        else:
            # not buffering: a bare lowercase letter starts a pinyin run
            if _pinyin_letter(ch) and not ctrl and not alt:
                self._composition_target = tgt
                self.buffer = ch.lower()
                self._update()
                return True
            return False

    # ---- commit / insert --------------------------------------------------
    def _commit(self, tgt, text):
        self._insert(tgt, text)
        self._reset()

    def _insert(self, tgt, text):
        try:
            if isinstance(tgt, Gtk.TextView):
                buf = tgt.get_buffer()
                buf.delete_selection(True, True)
                buf.insert_at_cursor(text)
            else:                              # Gtk.Editable (Entry, etc.)
                sel = tgt.get_selection_bounds()
                if sel:                        # (start, end) when text selected
                    tgt.delete_text(sel[0], sel[1])
                    tgt.set_position(sel[0])
                pos = tgt.get_position()
                # PyGObject overrides Gtk.Editable.insert_text to (text,
                # position) and supplies the C length argument itself; the
                # 3-argument form raises TypeError, which the except below
                # would swallow — committing a candidate into any Gtk.Entry
                # would then delete the selection and insert nothing at all.
                tgt.set_position(tgt.insert_text(text, pos))
        except Exception:
            pass

    # ---- candidate popup --------------------------------------------------
    def _reset(self):
        self.buffer = ""
        self.cands = []
        self.page = 0
        self._composition_target = None
        if self.popup:
            self.popup.hide()

    def _update(self):
        self.page = 0
        self.cands = self._lookup(self.buffer)
        self._show()

    def _ensure_popup(self):
        if self.popup:
            return
        self.popup = Gtk.Window(type=Gtk.WindowType.POPUP)
        self.popup.set_type_hint(Gdk.WindowTypeHint.TOOLTIP)
        self.popup.set_transient_for(self.win)
        fr = Gtk.Frame()
        fr.get_style_context().add_class("pyime")
        self._pop_label = Gtk.Label()
        self._pop_label.set_xalign(0)
        self._pop_label.set_margin_top(5)
        self._pop_label.set_margin_bottom(5)
        self._pop_label.set_margin_start(9)
        self._pop_label.set_margin_end(9)
        fr.add(self._pop_label)
        self.popup.add(fr)
        prov = Gtk.CssProvider()
        prov.load_from_data(
            b".pyime{background:#FCFBF8;border:1px solid #B3AD9E;}"
            b".pyime label{font-family:'Nimbus Sans','Noto Sans CJK SC',sans-serif;"
            b"font-size:15px;color:#1A1916;}")
        self._pop_label.get_style_context().add_provider(
            prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        fr.get_style_context().add_provider(
            prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _show(self):
        if not self.buffer:
            if self.popup:
                self.popup.hide()
            return
        self._ensure_popup()
        shown = self.cands[self.page * PAGE:self.page * PAGE + PAGE]
        parts = ["%d %s" % (i + 1, w) for i, w in enumerate(shown)]
        more = ""
        if len(self.cands) > PAGE:
            pages = (len(self.cands) + PAGE - 1) // PAGE
            more = "   (%d/%d  -/=)" % (self.page + 1, pages)
        txt = "%s   %s%s" % (self.buffer, "  ".join(parts) or "…", more)
        self._pop_label.set_text(txt)
        # Placing and showing the popup is COSMETIC and must never decide what
        # gets typed. _on_key claims the keystroke on the strength of the
        # composition and then calls this — so when _place raised, PyGObject
        # swallowed the exception, the handler counted as unhandled, and GTK
        # passed the raw pinyin on to the widget: every TextView in the OS
        # produced "nihao你好" instead of 你好. A popup that cannot be placed
        # is not shown; the keystroke is still ours.
        try:
            self._place()
            self.popup.show_all()
        except Exception:
            self.popup.hide()

    def _place(self):
        # Position at the caret for long-form editors, then keep the candidate
        # window inside the active monitor.  Anchoring below an entire
        # full-height TextView puts the popup beyond the bottom of the screen.
        tgt = self._focus_text()
        if tgt is None:
            return
        if isinstance(tgt, Gtk.TextView):
            # Gtk.TextView.get_window(win_type) SHADOWS the inherited
            # zero-argument Gtk.Widget.get_window, so the ordinary call raises
            # TypeError here — on every keystroke, in all eleven apps with a
            # text area, which is every long-form writing surface there is.
            gdkwin = tgt.get_window(Gtk.TextWindowType.WIDGET)
        else:
            gdkwin = tgt.get_window()
        gdkwin = gdkwin or self.win.get_window()
        if gdkwin is None:
            return
        res = gdkwin.get_origin()          # (x, y) or (ok, x, y) across bindings
        root_x, root_y = res[-2], res[-1]
        if isinstance(tgt, Gtk.TextView):
            buf = tgt.get_buffer()
            it = buf.get_iter_at_mark(buf.get_insert())
            caret = tgt.get_iter_location(it)
            wx, wy = tgt.buffer_to_window_coords(
                Gtk.TextWindowType.WIDGET, caret.x, caret.y)
            x = root_x + wx
            above_y = root_y + wy
            y = above_y + caret.height + 2
        else:
            alloc = tgt.get_allocation()
            x = root_x
            above_y = root_y
            y = root_y + alloc.height + 2

        _minimum, natural = self.popup.get_preferred_size()
        width = max(natural.width, 1)
        height = max(natural.height, 1)
        display = Gdk.Display.get_default()
        monitor = display.get_monitor_at_window(gdkwin) if display else None
        if monitor is not None:
            work = monitor.get_workarea()
            x = min(max(x + 6, work.x),
                    max(work.x + work.width - width, work.x))
            if y + height > work.y + work.height:
                y = above_y - height - 2
            y = min(max(y, work.y),
                    max(work.y + work.height - height, work.y))
        else:
            x += 6
        self.popup.move(int(x), int(y))
