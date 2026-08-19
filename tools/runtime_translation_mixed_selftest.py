#!/usr/bin/env python3
"""A translated sibling must not hide an untranslated formatted fragment."""

from pathlib import Path
import tempfile

import runtime_translation_check as gate


def scan(source):
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "sample.py"
        path.write_text(source, encoding="utf-8")
        return gate.scan(path, {"Prefix", "of %s"})


def main() -> None:
    bad = scan('def update(label, name):\n'
               '    label.set_text(_t("Prefix") + ("of %s" % name))\n')
    assert bad and bad[0][2] == "of %s"
    good = scan('def update(label, name):\n'
                '    label.set_text(_t("Prefix") + (_t("of %s") % name))\n')
    assert not good
    print("PASS translation checks classify each formatted subexpression")
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
