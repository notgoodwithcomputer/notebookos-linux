#!/usr/bin/env python3
"""Regression checks for equivalent GTK switch selectors."""

from theme_transition_check import (_selects_feedback_control, _selects_switch,
                                    _without_comments)


def main():
    assert "transition-duration" not in _without_comments(
        "/* historical: button { transition-duration: 0ms; } */")
    assert "/* literal */" in _without_comments(
        'label { -x: "/* literal */"; }')
    cases = [
        ("switch > slider", True, "child combinator"),
        ("window switch.slider-class slider", True, "qualified switch"),
        ("button, switch + label", True, "comma selector"),
        ("scale > slider", False, "unrelated slider"),
        (".switch > slider", False, "class named switch"),
    ]
    failed = 0
    for selector, expected, label in cases:
        got = _selects_switch(selector)
        if got != expected:
            failed += 1
            print("FAIL: %s (%r => %r)" % (label, selector, got))
        else:
            print("PASS: " + label)
    for selector, expected in (("button", True), ("entry:focus", True),
                               ("window button.flat, entry", True),
                               ("label", False), (".button", False)):
        got = _selects_feedback_control(selector)
        if got != expected:
            failed += 1
            print("FAIL: feedback control selector %r" % selector)
        else:
            print("PASS: feedback control selector %r" % selector)
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAILED"))
    return bool(failed)


if __name__ == "__main__":
    raise SystemExit(main())
