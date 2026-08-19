#!/usr/bin/env python3
"""Mutation checks for executable page-switch primitive detection."""

import ast
import page_switch_consistency_check as gate


def check(value, expected, label):
    if value != expected:
        print("FAIL: " + label)
        return 1
    print("PASS: " + label)
    return 0


def main():
    failures = 0
    fake = """
# TODO: migrate to PageSwitcher later
stack = Gtk.Stack()
stack.set_visible_child_name('next')
"""
    failures += check(gate._uses_primitive(ast.parse(fake)), False,
                      "a comment cannot launder a hand-rolled switch")
    failures += check(gate._uses_primitive(ast.parse(
        "from nbtransitions import PageSwitcher\nstack = Gtk.Stack()\n")),
        False, "an unused import cannot claim adoption")
    failures += check(gate._uses_primitive(ast.parse(
        "switcher = nbtransitions.PageSwitcher(stack, ['a', 'b'])\n")),
        True, "a real PageSwitcher construction is detected")
    failures += check(gate._uses_primitive(ast.parse(
        "nbtransitions.switch_page(stack, 'b', ['a', 'b'])\n")),
        True, "a real switch_page call is detected")
    print("RESULT: %s" % ("ALL PASS" if not failures else "FAILED"))
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
