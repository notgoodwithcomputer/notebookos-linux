#!/usr/bin/env python3
"""Headless checks for the native on-screen keyboard."""
import ast
import contextlib
import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, DE)
import osk  # noqa: E402


class Recorder:
    def __init__(self, mapped=None, fail_code=None):
        self.events = []
        self.mapped = mapped or {}
        self.fail_code = fail_code

    def keycode_for(self, sym):
        self.events.append(("lookup", sym))
        return self.mapped.get(sym, 0)

    def key(self, code, down):
        self.events.append(("key", code, down))
        if self.fail_code == code and down:
            raise RuntimeError("injected failure")

    def flush(self):
        self.events.append(("flush",))

    def unused(self):
        self.events.append(("unused",))
        return 200, (0, 0)

    def remap(self, code, values):
        self.events.append(("remap", code, tuple(values)))


class OSKCoreTests(unittest.TestCase):
    def test_keymap_group_level_and_cyrillic(self):
        table = {(24, 0, 0): ord("q"), (24, 0, 1): ord("Q"),
                 (24, 1, 0): 0x01000439, (24, 1, 1): 0x01000419}
        labels = osk.KeymapLabels(lambda c, g, l: table.get((c, g, l), 0), 1)
        self.assertEqual(labels.row((24,), 0), ["\u0439"])
        self.assertEqual(labels.row((24,), 1), ["\u0419"])
        labels.group = 0
        self.assertEqual(labels.row((24,), 0), ["q"])

    def test_shift_and_pages(self):
        state = osk.KeyboardState()
        state.tap_shift()
        self.assertEqual(state.intent(24), (24, True))
        self.assertEqual(state.intent(25), (25, False))
        state.tap_shift(); state.tap_shift()
        self.assertEqual(state.intent(26), (26, True))
        self.assertEqual(state.intent(27), (27, True))
        state.toggle_page(); self.assertEqual(state.page, "symbols")
        state.toggle_page(); self.assertEqual(state.page, "letters")

    def test_editing_controls_do_not_consume_one_shot_shift(self):
        state = osk.KeyboardState()
        state.tap_shift()
        self.assertEqual(state.control_intent(osk.SPECIAL["backspace"]),
                         (osk.SPECIAL["backspace"], False))
        self.assertEqual(state.shift, 1)
        self.assertEqual(state.control_intent(osk.SPECIAL["enter"]),
                         (osk.SPECIAL["enter"], False))
        self.assertEqual(state.shift, 1)
        self.assertEqual(state.intent(24), (24, True))
        self.assertEqual(state.shift, 0)

    def test_direct_printable_consumes_only_one_shot_shift(self):
        state = osk.KeyboardState()
        state.shift = 1
        self.assertTrue(state.consume_printable())
        self.assertEqual(state.shift, 0)
        state.shift = 2
        self.assertFalse(state.consume_printable())
        self.assertEqual(state.shift, 2)

    def test_shift_wrap_exact_order(self):
        rec = Recorder()
        osk.Injector(rec).keycode(24, True)
        self.assertEqual(rec.events,
            [("key", 50, True), ("key", 24, True), ("key", 24, False),
             ("key", 50, False), ("flush",)])

    def test_remap_restored_even_when_injection_fails(self):
        rec = Recorder(fail_code=200)
        with self.assertRaises(RuntimeError):
            osk.Injector(rec).character("\u00e9")
        sym = 0x01000000 | ord("\u00e9")
        self.assertEqual(rec.events,
            [("lookup", sym), ("unused",), ("remap", 200, (sym, 0)),
             ("key", 200, True), ("flush",), ("remap", 200, (0, 0))])

    def test_long_press_select_and_cancel(self):
        rec = Recorder()
        injector = osk.Injector(rec)
        model = osk.LongPressModel()
        model.begin("e")
        self.assertEqual(model.advance(osk.HOLD_MS - 1), ())
        entries = model.advance(1)
        self.assertEqual(entries, osk.TABLE["e"])
        self.assertTrue(model.select(0, injector.character))
        self.assertEqual(rec.events[-1], ("remap", 200, (0, 0)))
        before = list(rec.events)
        model.begin("e"); model.advance(osk.HOLD_MS); model.cancel()
        self.assertEqual(rec.events, before)

    def test_window_contract_static_and_optional_construct(self):
        path = os.path.join(DE, "osk.py")
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        tree = ast.parse(source)
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)]
        self.assertTrue(any(n.func.attr == "set_accept_focus" and
                            isinstance(n.args[0], ast.Constant) and
                            n.args[0].value is False for n in calls))
        self.assertIn("Gdk.WindowTypeHint.DOCK", source)
        self.assertIn('self.state.shift == 2 else "\\u21e7"', source)
        self.assertIn('add_class("locked")', source)
        ok, _argv = osk.Gtk.init_check([])
        if not ok:
            self.skipTest("GTK display unavailable; static contract passed")
        rec = Recorder()
        win = osk.OSKWindow(osk.Injector(rec))
        self.assertFalse(win.get_accept_focus())
        win.destroy()


def red_proofs():
    """Run deliberately broken local models, capture their expected reds."""
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        try:
            rec = Recorder(fail_code=200)
            sym = 0x01000000 | ord("\u00e9")
            code, old = rec.unused()
            rec.remap(code, (sym, 0))
            try:
                rec.key(code, True)
            except RuntimeError:
                pass                    # deliberately missing finally restore
            assert rec.events[-1] == ("remap", 200, old)
        except AssertionError:
            print("RED-PROOF 1 PASS: missing remap finally was caught")
        try:
            state = osk.KeyboardState()
            state.shift = 2
            state.shift = 0              # deliberately consume caps latch
            assert state.intent(24) == (24, True)
        except AssertionError:
            print("RED-PROOF 2 PASS: broken caps latch was caught")
    captured = output.getvalue()
    if captured.count("RED-PROOF") != 2:
        raise AssertionError("red proofs did not both fail as designed")
    print(captured, end="")


if __name__ == "__main__":
    red_proofs()
    unittest.main(verbosity=2)
