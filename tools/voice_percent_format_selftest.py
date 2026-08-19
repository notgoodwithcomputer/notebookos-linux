#!/usr/bin/env python3
"""Old-style formatted UI prose receives the same voice review as f-strings."""
import ast
import voice_check as gate


def expr(source):
    return ast.parse(source, mode="eval").body


def main():
    for source in ('"Your %s" % name', '"Your %(name)s" % values',
                   '"Your %s %s" % (first, last)'):
        text = gate.expression_text(expr(source))
        assert text and gate.judge(text), (source, text)
    assert gate.expression_text(expr("count % 2")) is None
    print("PASS positional, named and tuple percent formatting is voice-checked")
    print("PASS numeric modulo remains outside visible-string analysis")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
