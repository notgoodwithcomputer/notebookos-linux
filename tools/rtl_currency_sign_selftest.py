#!/usr/bin/env python3
"""Leading signs stay at-risk when a currency ET precedes the digits."""
import ast
import rtl_check as gate


def risky(text):
    tree = ast.parse("def f(label):\n label.set_text(%r)\n" % text)
    return bool(gate._visible_at_risk(tree))


for value in ("+100", "+$100", "−€950.00", "-$42", "+$%d", "−€%.2f"):
    assert risky(value), value
assert not risky("$100")
assert not risky("€950.00")
print("PASS signed currency figures require an LTR isolate")
print("RESULT: PASS")
