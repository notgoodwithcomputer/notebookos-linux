#!/usr/bin/env python3
"""Visible f-strings and .format calls must enter the catalog inventory."""

from pathlib import Path
import tempfile

import i18n_coverage_check as gate


def shown(source):
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "sample.py"
        path.write_text(source, encoding="utf-8")
        return gate.shown_strings(path)


def main() -> None:
    assert "Delete %s" in shown(
        'def f(name):\n    return Gtk.Label(label=f"Delete {name}")\n')
    assert "Delete %s" in shown(
        'def f(name):\n    return Gtk.Label(label="Delete {}".format(name))\n')
    try:
        shown('def broken(:\n')
    except SyntaxError:
        pass
    else:
        raise AssertionError("syntax errors erased all coverage")
    print("PASS dynamic visible strings and parse failures cannot vanish")
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
